"""Collect the nationwide passenger-car study locally, once or daily."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from threading import Event

from bama_scraper.logging_config import configure_logging

from .config import (
    NATIONWIDE_STUDY_PROFILE,
    REPO_ROOT,
    ConfigFileError,
    MonitorConfig,
    load_config_strict,
    validate_study_profile_paths,
)
from .daily_snapshot import export_daily_snapshot
from .db import connect, parse_ts, utcnow
from .repository import Repository
from .runner import DailyRunner
from .scheduler_daemon import ProfileMismatchError, SchedulerDaemon

PROFILE = REPO_ROOT / "deploy/profiles/nationwide-passenger.yaml"


def _config(state: Path) -> MonitorConfig:
    # Explicit absolute paths keep both the legacy database environment variable
    # and subsequent changes to the process working directory out of this study.
    state = state.expanduser().resolve()
    cfg = load_config_strict(
        PROFILE,
        database_url=f"sqlite:///{state / 'monitor.sqlite'}",
        output_dir=state / "evidence",
        reports_dir=state / "reports",
        daily_snapshot_dir=state / "snapshots",
        lock_dir=state / "locks",
    )
    if cfg.study_profile != NATIONWIDE_STUDY_PROFILE:
        raise ConfigFileError(f"Expected the nationwide passenger profile in {PROFILE}")
    validate_study_profile_paths(cfg)
    return cfg


def _manual(cfg: MonitorConfig) -> int:
    with closing(connect(cfg.database_url)) as db:
        SchedulerDaemon(cfg).validate_profile_state(db)
        if db.fetchone("SELECT 1 FROM production_schedule LIMIT 1") or db.fetchone(
            "SELECT 1 FROM monitoring_runs WHERE trigger_type<>'manual' OR is_synthetic<>0 LIMIT 1"
        ):
            raise ProfileMismatchError(
                "The manual data directory contains scheduled or test history"
            )
        runner = DailyRunner(cfg, db=db)
        outcome = asyncio.run(runner.run_daily(trigger_type="manual"))
        snapshot = {}
        if outcome.run_id is not None and not outcome.skipped:
            snapshot = export_daily_snapshot(db, cfg, outcome.run_id, require_valid=True)
        print(
            json.dumps(
                {"trigger_type": "manual", **outcome.as_dict(), "snapshot": snapshot},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
        return 0 if snapshot.get("exported") else 1


def _daily(cfg: MonitorConfig, stop: Event) -> int:
    daemon = SchedulerDaemon(cfg)
    today = utcnow().astimezone(daemon.zone).date()
    with closing(connect(cfg.database_url)) as db:
        daemon.validate_profile_state(db)
        if db.fetchone(
            "SELECT 1 FROM monitoring_runs WHERE trigger_type NOT IN ('scheduled','catch_up')"
            " OR is_synthetic<>0 LIMIT 1"
        ):
            raise ProfileMismatchError("The daily data directory contains manual or test history")
        daemon.register_schedule()
        schedule = Repository(db).get_production_schedule(cfg.production_schedule_name)
        started = parse_ts(schedule["production_schedule_started_at"]) if schedule else None
        day = started.astimezone(daemon.zone).date() if started else today
        # Complete missed dates since the last recorded job, including dates
        # when this process was offline. Recheck today's slot on a same-day restart.
        latest = db.fetchone(
            "SELECT MAX(day) AS day FROM ("
            " SELECT scheduled_date_paris AS day FROM monitoring_runs"
            " WHERE production_schedule_name=? AND is_synthetic=0"
            " UNION ALL SELECT scheduled_date_paris AS day FROM missed_schedule_slots"
            " WHERE production_schedule_name=?) AS slots",
            [cfg.production_schedule_name, cfg.production_schedule_name],
        )
        if latest and latest["day"]:
            day = min(today, date.fromisoformat(latest["day"]) + timedelta(days=1))

    while not stop.is_set():
        now = utcnow()
        seconds = (daemon.slot_for_paris_date(day) - now).total_seconds()
        if seconds > 0:
            stop.wait(min(seconds, cfg.watchdog_interval_seconds))
            continue
        try:
            result = daemon.run_due(slot_date=day, now=now)
        except sqlite3.OperationalError as exc:
            print(f"Cannot access the daily database for {day}: {exc}", file=sys.stderr, flush=True)
            stop.wait(cfg.watchdog_interval_seconds)
            continue
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        if result["status"] == "profile_error":
            return 1
        if result["status"] == "locked" or (
            result["status"] == "failed" and not result.get("run_id")
        ):
            stop.wait(cfg.watchdog_interval_seconds)
        else:
            day += timedelta(days=1)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", action="store_true", help="stay running for 13:00 Europe/Paris")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("bama-nationwide-data"),
        help="local data directory (default: ./bama-nationwide-data)",
    )
    args = parser.parse_args(argv)
    mode = "daily" if args.daily else "manual"
    cfg = _config(args.state_dir / mode)
    cfg.ensure_dirs()
    configure_logging(cfg.log_level)
    print(f"{mode.capitalize()} collection uses {cfg.sqlite_path.parent}", flush=True)
    if not args.daily:
        return _manual(cfg)

    print(f"Collecting daily at {cfg.run_at} {cfg.timezone}. Press Ctrl+C to stop.", flush=True)
    stop = Event()
    # Finish the active observation before stopping, so interruption during a
    # long census does not strand a running row or an unpublished snapshot.
    previous = {
        sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        return _daily(cfg, stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigFileError, ProfileMismatchError, sqlite3.DatabaseError, OSError) as exc:
        raise SystemExit(f"Cannot run the nationwide monitor: {exc}") from None
