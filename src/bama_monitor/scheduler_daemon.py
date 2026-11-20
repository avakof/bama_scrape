"""Long-running scheduler: fires the daily production run at 13:00 Europe/Paris.

Design decisions worth stating, because each one is a way this could quietly do
the wrong thing:

**The timezone is explicit, never the machine's.** ``CronTrigger`` is constructed
with ``ZoneInfo("Europe/Paris")``. If the Mac is set to Tehran, London or UTC, the
job still fires at 13:00 Paris. A scheduler that inherits the system timezone
looks correct on the developer's laptop and silently drifts on any other machine.

**A missed slot is reconciled, not invented.** On startup the daemon asks whether
today's slot already has a *genuine production* run. If not, and the slot has
passed, it starts a ``catch_up`` run whose ``scheduled_for`` is the intended 13:00
and whose ``started_at`` is the real time — never a fabricated 13:00 observation.
Past the grace window it records a missed slot and alerts instead.

**Manual runs do not count.** The reconciliation lookup is scoped to production
triggers, so a manual run attributed to today's slot neither satisfies nor blocks
the scheduled execution.

**A long timer is not trusted.** ``CronTrigger`` fires precisely on a machine that
stays awake, and not at all on one that does not: APScheduler waits on
``threading.Event.wait``, whose timeout on macOS is measured by
``mach_absolute_time()``, which stops while the machine sleeps. Observed here, not
theorised -- 131 hours of sleep against 298 of wall clock meant a 20-hour timer had
barely advanced when its slot arrived, and the job simply did not fire. A
60-second watchdog re-decides from the wall clock instead, so drift is bounded by
one interval rather than by the lid.

**Overlap is prevented at four layers.** APScheduler's ``max_instances=1``, the
filesystem lock, the database lock row, and (on PostgreSQL) a session advisory
lock. Any one of them can fail; all four failing at once is the scenario this does
not defend against.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bama_scraper.logging_config import get_logger

from .alerts import AlertManager
from .config import (
    MonitorConfig,
    load_config,
    load_config_strict,
    validate_study_profile_paths,
)
from .db import Database, connect, parse_ts, utcnow
from .locking import LockUnavailable, run_lock
from .models import AlertSeverity, RunHealth, TriggerType
from .repository import Repository
from .scheduler import next_scheduled_slot, parse_run_time, slot_for_paris_date

log = get_logger("bama_monitor.scheduler_daemon")

#: A one-shot invocation must be serialised against another one for the same
#: state directory. DailyRunner has its own lock for the pipeline; this outer
#: lock covers the reconcile-and-decide step so two workflows cannot both see an
#: empty slot and attempt to fill it.
RUN_DUE_LOCK_NAME = "bama_monitor_run_due"


class ProfileMismatchError(RuntimeError):
    """The database's recorded series does not match this configuration."""


class SchedulerDaemon:
    """Owns the production schedule for one search configuration."""

    def __init__(self, cfg: MonitorConfig) -> None:
        self.cfg = cfg
        self.zone = ZoneInfo(cfg.timezone)
        # Identifies this daemon process in every run it creates, so a run can be
        # traced back to the scheduler instance that fired it.
        self.instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.scheduler: BlockingScheduler | None = None
        self._stopping = False

    # -- slot arithmetic ---------------------------------------------------

    def slot_for_date(self, day: datetime) -> datetime:
        """The 13:00 Paris instant for the Paris date of ``day``, as UTC.

        Built by localising a naive wall-clock time in the target zone, so the
        UTC instant moves across DST while the local time stays fixed. ``fold=0``
        resolves the ambiguous repeated hour in autumn to the first occurrence.
        """
        target = parse_run_time(self.cfg.run_at)
        local_date = day.astimezone(self.zone).date()
        naive = datetime.combine(local_date, target)
        return naive.replace(tzinfo=self.zone, fold=0).astimezone(UTC)

    def slot_for_paris_date(self, day: date) -> datetime:
        """The same slot, addressed by its Paris calendar date."""
        return slot_for_paris_date(self.cfg, day)

    # -- profile integrity -------------------------------------------------

    def validate_profile_state(self, db: Database) -> None:
        """Refuse to append a profile series to a different series' history.

        The database is the series' identity, not its filename. A restored copy
        of the legacy database is still the legacy history even when it arrives
        as ``monitor.sqlite`` in a new state directory, and a manual row from
        that database is still a legacy observation. Checking only rows whose
        production schedule name already matches this profile misses exactly
        that case.

        For a non-legacy profile, every existing schedule and run must therefore
        belong to the same configuration hash. Same-profile manual rows are
        allowed: they carry the current hash with no production schedule name.
        Legacy deliberately keeps its previous tolerance: its old database may
        contain runs from earlier monitor versions, and refusing to start after
        an upgrade would be worse than the incomparability the hash records.
        """
        if self.cfg.study_profile == "legacy":
            return
        current = self.cfg.configuration_hash()
        schedule = db.fetchone(
            "SELECT name, search_configuration_hash FROM production_schedule"
            " WHERE name <> ?"
            "    OR search_configuration_hash IS NULL"
            "    OR search_configuration_hash <> ?"
            " ORDER BY name LIMIT 1",
            [self.cfg.production_schedule_name, current],
        )
        if schedule is not None:
            raise ProfileMismatchError(
                f"database contains production schedule {schedule['name']!r} with "
                f"configuration_hash {schedule['search_configuration_hash']!r}, not "
                f"{current!r} for study_profile={self.cfg.study_profile!r}; "
                "refusing to mix series histories"
            )
        mismatched = db.fetchone(
            "SELECT id, production_schedule_name, trigger_type, configuration_hash"
            " FROM monitoring_runs"
            " WHERE configuration_hash IS NULL"
            "    OR configuration_hash <> ?"
            "    OR (production_schedule_name IS NOT NULL AND production_schedule_name <> ?)"
            " ORDER BY id LIMIT 1",
            [current, self.cfg.production_schedule_name],
        )
        if mismatched is not None:
            raise ProfileMismatchError(
                f"run {mismatched['id']} "
                f"(trigger={mismatched['trigger_type']!r}, "
                f"schedule={mismatched['production_schedule_name']!r}) used "
                f"configuration_hash {mismatched['configuration_hash']!r}, not "
                f"{current!r}; refusing to mix series histories"
            )

    def todays_slot(self, *, now: datetime | None = None) -> datetime:
        return self.slot_for_date(now or utcnow())

    def next_slot(self, *, now: datetime | None = None) -> datetime:
        return next_scheduled_slot(self.cfg, reference=now or utcnow())

    def describe_next(self, *, now: datetime | None = None) -> dict[str, str]:
        instant = self.next_slot(now=now)
        return {
            "europe_paris": instant.astimezone(self.zone).isoformat(),
            "utc": instant.astimezone(UTC).isoformat(),
            "local_machine": instant.astimezone().isoformat(),
        }

    # -- reconciliation ----------------------------------------------------

    def reconcile(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Decide what, if anything, today's slot still needs.

        Returns a decision rather than acting, so the same logic is testable
        without a database write or a network call.
        """
        now = now or utcnow()
        slot = self.todays_slot(now=now)
        db = connect(self.cfg.database_url)
        try:
            repo = Repository(db)
            existing = repo.genuine_production_run_for_slot(
                scheduled_for=slot,
                configuration_hash=self.cfg.configuration_hash(),
                production_schedule_name=self.cfg.production_schedule_name,
            )
            if existing is not None:
                return {
                    "action": "none",
                    "slot": slot.isoformat(),
                    "reason": (
                        f"genuine production run {existing['id']} already exists for this slot"
                    ),
                    "existing_run_id": int(existing["id"]),
                }

            if now < slot:
                return {
                    "action": "wait",
                    "slot": slot.isoformat(),
                    "reason": (
                        f"slot has not arrived; {(slot - now).total_seconds() / 60:.0f} "
                        "minute(s) to go"
                    ),
                }

            late_minutes = (now - slot).total_seconds() / 60.0
            if late_minutes <= self.cfg.catch_up_grace_minutes:
                return {
                    "action": "catch_up",
                    "slot": slot.isoformat(),
                    "minutes_late": round(late_minutes, 1),
                    "reason": (
                        f"slot passed {late_minutes:.0f} min ago, inside the "
                        f"{self.cfg.catch_up_grace_minutes} min grace window"
                    ),
                }
            return {
                "action": "record_missed",
                "slot": slot.isoformat(),
                "minutes_late": round(late_minutes, 1),
                "reason": (
                    f"slot passed {late_minutes:.0f} min ago, beyond the "
                    f"{self.cfg.catch_up_grace_minutes} min grace window; the "
                    "observation cannot be recovered and will not be fabricated"
                ),
            }
        finally:
            db.close()

    def record_missed_slot(self, decision: dict[str, Any]) -> None:
        """Persist the fact that a slot was missed, and alert."""
        db = connect(self.cfg.database_url)
        try:
            repo = Repository(db)
            slot = parse_ts(decision["slot"])
            repo.record_missed_slot(
                {
                    "production_schedule_name": self.cfg.production_schedule_name,
                    "scheduled_for": slot,
                    "scheduled_date_paris": (
                        slot.astimezone(self.zone).date().isoformat() if slot else None
                    ),
                    "detected_at": utcnow(),
                    "minutes_late": decision.get("minutes_late"),
                    "resolution": "outside_grace_window",
                    "note": decision.get("reason"),
                }
            )
            AlertManager(repo, self.cfg.alerts).raise_alert(
                "scheduled_slot_missed",
                f"the {decision['slot']} slot was missed: {decision.get('reason')}",
                severity=AlertSeverity.WARNING,
            )
            log.warning("scheduler.slot_missed", slot=decision["slot"])
        finally:
            db.close()

    # -- execution ---------------------------------------------------------

    def execute(
        self,
        *,
        trigger_type: TriggerType,
        scheduled_for: datetime | None = None,
        discovery_scraper: Any = None,
        detail_scraper_factory: Any = None,
    ) -> dict[str, Any]:
        """Run one production job. Called by the trigger and by reconciliation.

        The scraper arguments exist for deterministic offline tests; production
        calls leave them unset and the runner constructs its normal adapters.
        """
        from .runner import DailyRunner

        slot = scheduled_for or self.todays_slot()
        log.info(
            "scheduler.job_start",
            trigger=str(trigger_type),
            slot=slot.isoformat(),
            instance=self.instance_id,
        )
        db = connect(self.cfg.database_url)
        runner = DailyRunner(
            self.cfg,
            db=db,
            discovery_scraper=discovery_scraper,
            detail_scraper_factory=detail_scraper_factory,
        )
        try:
            outcome = asyncio.run(
                runner.run_daily(
                    scheduled_for=slot,
                    trigger_type=str(trigger_type),
                    is_synthetic=False,
                    production_schedule_name=self.cfg.production_schedule_name,
                    scheduler_instance_id=self.instance_id,
                )
            )
            payload = outcome.as_dict()
            payload["snapshot"] = self._after_run(db, outcome)
            payload["trigger_type"] = str(trigger_type)
            payload["scheduled_for"] = slot.isoformat()
            payload["run_times"] = self._run_times(db, outcome.run_id)
            log.info(
                "scheduler.job_finished",
                run_id=outcome.run_id,
                status=str(outcome.status),
                trigger=str(trigger_type),
            )
            return payload
        except Exception as exc:  # noqa: BLE001 - a scheduler must not die on a job
            log.error("scheduler.job_failed", error=repr(exc))
            try:
                AlertManager(Repository(db), self.cfg.alerts).raise_alert(
                    "scheduled_run_failed",
                    f"{trigger_type} run raised {exc!r}",
                    severity=AlertSeverity.CRITICAL,
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                "error": repr(exc),
                "trigger_type": str(trigger_type),
                "scheduled_for": slot.isoformat(),
                "snapshot": {
                    "attempted": False,
                    "exported": False,
                    "latest_valid_updated": False,
                    "error": repr(exc),
                },
            }
        finally:
            db.close()

    def _after_run(self, db: Any, outcome: Any) -> dict[str, Any]:
        """Export the daily snapshot and advance the history marker.

        Returns a machine-readable record of what publication actually did. A
        run that finished is not the same as a day that was published, and the
        one-shot exit code needs the difference.
        """
        from .daily_snapshot import export_daily_snapshot, read_latest_pointer

        summary: dict[str, Any] = {
            "attempted": False,
            "exported": False,
            "latest_valid_updated": False,
            "destination": None,
            "validation": None,
            "pointer_run_id": None,
            "error": None,
        }
        if outcome.run_id is None:
            return summary
        summary["attempted"] = True
        published = False
        try:
            result = export_daily_snapshot(db, self.cfg, outcome.run_id, require_valid=True)
            published = bool(result.get("exported"))
            summary.update(
                {
                    "exported": published,
                    "latest_valid_updated": bool(result.get("latest_valid_updated")),
                    "destination": result.get("destination"),
                    "validation": result.get("validation"),
                }
            )
            log.info(
                "scheduler.snapshot",
                run_id=outcome.run_id,
                exported=published,
                latest_valid_updated=result.get("latest_valid_updated"),
            )
        except Exception as exc:  # noqa: BLE001 - a failed export must not lose the run
            summary["error"] = repr(exc)
            log.error("scheduler.snapshot_failed", run_id=outcome.run_id, error=repr(exc))

        try:
            pointer = read_latest_pointer(self.cfg)
            summary["pointer_run_id"] = pointer.get("run_id") if pointer else None
        except Exception:  # noqa: BLE001 - a damaged pointer is reported by omission
            summary["pointer_run_id"] = None

        repo = Repository(db)
        schedule = repo.get_production_schedule(self.cfg.production_schedule_name)
        first_id = schedule.get("first_genuine_scheduled_run_id") if schedule else None
        # The boundary marks the first day a reader can actually use, so it needs
        # the snapshot to have been published — not merely a run that finished.
        # A genuine run that fails its validity gate is real history and stays in
        # `monitoring_runs`, but it is not the start of the mother dataset.
        if first_id is None and published and str(outcome.status) == "valid":
            repo.upsert_production_schedule(
                {
                    "name": self.cfg.production_schedule_name,
                    "search_configuration_hash": self.cfg.configuration_hash(),
                    "timezone": self.cfg.timezone,
                    "run_at_local": self.cfg.run_at,
                    "first_genuine_scheduled_run_id": outcome.run_id,
                    "host_name": socket.gethostname(),
                    "notes": "first genuine scheduled execution",
                }
            )
        return summary

    @staticmethod
    def _run_times(db: Any, run_id: int | None) -> dict[str, Any]:
        """Actual execution times and native labels from the run row.

        Only real timestamp columns are parsed. ``scheduled_date_paris`` is a
        calendar label, ``scheduled_hour_paris`` is ``HH-MM`` text and
        ``snapshot_export_path`` is a path; running them through the timestamp
        parser merely fails and hands the value back, blurring the contract for
        callers that need to distinguish a date from an instant.
        """
        if run_id is None:
            return {}
        row = Repository(db).get_run(run_id) or {}
        times = {
            key: row.get(key)
            for key in ("scheduled_date_paris", "scheduled_hour_paris", "snapshot_export_path")
        }
        for key in (
            "scheduled_for",
            "started_at",
            "finished_at",
            "discovery_started_at",
            "discovery_finished_at",
            "detail_started_at",
            "detail_finished_at",
        ):
            stamp = parse_ts(row.get(key))
            times[key] = stamp.isoformat() if stamp else None
        return times

    # -- portable one-shot -------------------------------------------------

    def run_due(
        self,
        *,
        slot_date: date,
        now: datetime | None = None,
        db: Database | None = None,
        discovery_scraper: Any = None,
        detail_scraper_factory: Any = None,
    ) -> dict[str, Any]:
        """Run (or explicitly decline) one slot named by its Paris date.

        This is the GitHub Actions entry point, not a daemon: it decides once,
        acts once and exits. The intended slot is always rebuilt from the
        external event's Paris date, so the stored ``scheduled_for`` is the
        original 13:00 Paris instant, while ``started_at``/``finished_at``
        remain the actual execution times.
        """
        checked_at = (now or utcnow()).astimezone(UTC)
        slot = self.slot_for_paris_date(slot_date)
        base: dict[str, Any] = {
            "event": "run_due",
            "study_profile": self.cfg.study_profile,
            "production_schedule_name": self.cfg.production_schedule_name,
            "slot_date_paris": slot_date.isoformat(),
            "slot_utc": slot.isoformat(),
            "checked_at_utc": checked_at.isoformat(),
            "timezone": self.cfg.timezone,
            "run_at_local": self.cfg.run_at,
        }
        self.cfg.ensure_dirs()
        own_db = db is None
        connection = db or connect(self.cfg.database_url)
        try:
            try:
                self.validate_profile_state(connection)
            except ProfileMismatchError as exc:
                return {
                    **base,
                    "status": "profile_error",
                    "action": "refuse",
                    "exit_code": 1,
                    "reason": str(exc),
                }
            try:
                with run_lock(connection, self.cfg.lock_dir, name=RUN_DUE_LOCK_NAME):
                    return self._run_due_locked(
                        connection,
                        base=base,
                        slot=slot,
                        checked_at=checked_at,
                        discovery_scraper=discovery_scraper,
                        detail_scraper_factory=detail_scraper_factory,
                    )
            except LockUnavailable as exc:
                return {
                    **base,
                    "status": "locked",
                    "action": "refuse",
                    "exit_code": 1,
                    "reason": str(exc),
                }
        finally:
            if own_db:
                connection.close()

    def _run_due_locked(
        self,
        db: Database,
        *,
        base: dict[str, Any],
        slot: datetime,
        checked_at: datetime,
        discovery_scraper: Any,
        detail_scraper_factory: Any,
    ) -> dict[str, Any]:
        repo = Repository(db)
        existing = repo.genuine_production_run_for_slot(
            scheduled_for=slot,
            configuration_hash=self.cfg.configuration_hash(),
            production_schedule_name=self.cfg.production_schedule_name,
        )
        if existing is not None:
            return self._existing_run_result(db, existing, base=base)

        if checked_at < slot:
            return {
                **base,
                "status": "not_due",
                "action": "wait",
                "exit_code": 0,
                "minutes_until_slot": round((slot - checked_at).total_seconds() / 60.0, 1),
                "reason": "the intended Paris slot has not arrived; nothing was observed",
            }

        late_minutes = (checked_at - slot).total_seconds() / 60.0
        if late_minutes > self.cfg.catch_up_grace_minutes:
            decision = {
                "slot": slot.isoformat(),
                "minutes_late": round(late_minutes, 1),
                "reason": (
                    f"slot passed {late_minutes:.0f} min ago, beyond the "
                    f"{self.cfg.catch_up_grace_minutes} min grace window; the "
                    "observation cannot be recovered and will not be fabricated"
                ),
            }
            self.record_missed_slot(decision)
            return {
                **base,
                "status": "missed",
                "action": "record_missed",
                "exit_code": 1,
                "minutes_late": round(late_minutes, 1),
                "reason": decision["reason"],
            }

        trigger = (
            TriggerType.SCHEDULED
            if late_minutes <= self.cfg.on_time_grace_minutes
            else TriggerType.CATCH_UP
        )
        payload = self.execute(
            trigger_type=trigger,
            scheduled_for=slot,
            discovery_scraper=discovery_scraper,
            detail_scraper_factory=detail_scraper_factory,
        )
        return self._executed_result(db, payload, base=base, trigger=trigger)

    def _publication_state(
        self,
        run: dict[str, Any],
        *,
        pointer: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Did this run publish, and can its archive be verified locally?

        A GitHub restore downloads only the newest snapshot. An older, genuinely
        published slot keeps its publication path in the database but its
        archive may not exist on this machine at all. The database is the source
        of truth: ``snapshot_export_path`` is written only after an atomic export
        succeeded, so a non-empty path is evidence of publication even when the
        directory is remote.

        That does not excuse a broken *local* archive. If a destination directory
        is present but incomplete, or the current ``latest_valid.json`` target
        has no manifest, reporting success would hide a damaged restore.
        """
        run_id = int(run["id"])
        destination = run.get("snapshot_export_path")
        pointer_id = pointer.get("run_id") if pointer else None
        verified = False
        reason = "no recorded publication or latest-valid snapshot"
        if destination and pointer and pointer.get("path"):
            # The restored pointer and manifest name the same published run.
            # Only earlier archives may be absent from the local checkpoint.
            try:
                latest_dir = Path(pointer["path"])
                latest = json.loads((latest_dir / "manifest.json").read_text(encoding="utf-8"))
                if latest["run_id"] != pointer_id:
                    raise ValueError("latest-valid manifest does not match the pointer")
                folder = Path(destination)
                if pointer_id == run_id:
                    verified = folder.resolve() == latest_dir.resolve()
                elif folder.exists():
                    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                    verified = manifest["run_id"] == run_id
                else:
                    latest_slot = parse_ts(pointer.get("scheduled_for"))
                    original_slot = parse_ts(run.get("scheduled_for"))
                    verified = bool(latest_slot and original_slot and original_slot < latest_slot)
                reason = "slot already published" if verified else "inconsistent publication record"
            except (OSError, KeyError, TypeError, ValueError) as exc:
                reason = f"could not verify the published snapshot: {exc}"
        return {
            "exported": bool(destination),
            "destination": destination,
            "pointer_run_id": pointer_id,
            "verified": verified,
            "reason": reason,
        }

    def _existing_run_result(
        self, db: Database, run: dict[str, Any], *, base: dict[str, Any]
    ) -> dict[str, Any]:
        """Report a slot that already has a genuine run, without duplicating it."""
        from .daily_snapshot import read_latest_pointer

        run_id = int(run["id"])
        run_status = str(run.get("status") or "")
        pointer = read_latest_pointer(self.cfg)
        publication = self._publication_state(run, pointer=pointer)
        result: dict[str, Any] = {
            **base,
            "action": "none",
            "run_id": run_id,
            "run_status": run_status,
            "run_times": self._run_times(db, run_id),
            "snapshot": publication,
        }
        if run_status == str(RunHealth.VALID) and publication["verified"]:
            return {
                **result,
                "status": "already_settled",
                "exit_code": 0,
                "reason": publication["reason"],
            }
        if run_status == str(RunHealth.VALID):
            return {
                **result,
                "status": "previous_run_unpublished",
                "exit_code": 1,
                "reason": publication["reason"],
            }
        if run_status == str(RunHealth.RUNNING):
            return {
                **result,
                "status": "previous_run_pending",
                "exit_code": 1,
                "reason": (
                    "a run for this slot is still marked running; refusing to start "
                    "a second observation of the same slot"
                ),
            }
        return {
            **result,
            "status": "previous_run_failed",
            "exit_code": 1,
            "reason": (
                f"the genuine run for this slot ended with status {run_status!r}; "
                "a previous failure must not be reported as success"
            ),
        }

    def _executed_result(
        self,
        db: Database,
        payload: dict[str, Any],
        *,
        base: dict[str, Any],
        trigger: TriggerType,
    ) -> dict[str, Any]:
        run_id = payload.get("run_id")
        snapshot = payload.get("snapshot") or {}
        run_status = str(payload.get("status") or "")
        result: dict[str, Any] = {
            **base,
            "action": str(trigger),
            "trigger_type": str(trigger),
            "run_id": run_id,
            "run_status": run_status,
            "run_times": payload.get("run_times") or self._run_times(db, run_id),
            "snapshot": snapshot,
            "discovered_count": payload.get("discovered_count"),
        }
        if payload.get("error") or run_id is None:
            return {
                **result,
                "status": "failed",
                "exit_code": 1,
                "reason": str(payload.get("error") or "the runner produced no run row"),
            }
        if run_status != str(RunHealth.VALID):
            return {
                **result,
                "status": "invalid",
                "exit_code": 1,
                "reason": f"the run ended with status {run_status!r}; nothing was published",
            }
        destination = snapshot.get("destination")
        published = bool(
            snapshot.get("exported")
            and (snapshot.get("validation") or {}).get("valid")
            and destination
            and (Path(destination) / "manifest.json").is_file()
            and snapshot.get("latest_valid_updated")
        )
        if not published:
            return {
                **result,
                "status": "unpublished",
                "exit_code": 1,
                "reason": (
                    "the run is valid but its snapshot/latest-valid pointer was not "
                    "published; refusing to report success"
                ),
            }
        return {
            **result,
            "status": "completed",
            "exit_code": 0,
            "reason": "the intended slot was observed and published",
        }

    # -- lifecycle ---------------------------------------------------------

    def register_schedule(self) -> None:
        """Record that this machine owns the production schedule."""
        db = connect(self.cfg.database_url)
        try:
            repo = Repository(db)
            existing = repo.get_production_schedule(self.cfg.production_schedule_name)
            payload: dict[str, Any] = {
                "name": self.cfg.production_schedule_name,
                "search_configuration_hash": self.cfg.configuration_hash(),
                "timezone": self.cfg.timezone,
                "run_at_local": self.cfg.run_at,
                "host_name": socket.gethostname(),
                "installed_by": os.environ.get("USER"),
            }
            if not existing or not existing.get("production_schedule_started_at"):
                payload["production_schedule_started_at"] = utcnow()
                payload["notes"] = (
                    "genuine scheduled history starts here; earlier runs are manual "
                    "or test executions"
                )
            repo.upsert_production_schedule(payload)
        finally:
            db.close()

    def start(self) -> int:
        """Install the trigger, reconcile, then block."""
        self.register_schedule()
        hour, minute = self.cfg.run_at.split(":")[0], self.cfg.run_at.split(":")[1]
        trigger = CronTrigger(hour=int(hour), minute=int(minute), timezone=self.zone)
        self.scheduler = BlockingScheduler(
            timezone=self.zone,
            executors={"default": ThreadPoolExecutor(1)},
            job_defaults={
                # One at a time, and a run that fires while another is going is
                # collapsed rather than queued: two discoveries of the same slot
                # would double-count observations.
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": self.cfg.catch_up_grace_minutes * 60,
            },
        )
        self.scheduler.add_job(
            self._scheduled_job,
            trigger=trigger,
            id="bama-daily-production",
            name=f"Bama daily production run at {self.cfg.run_at} {self.cfg.timezone}",
            replace_existing=True,
        )
        # The mechanism that actually holds on a machine that sleeps.
        self.scheduler.add_job(
            self._watchdog,
            trigger=IntervalTrigger(seconds=self.cfg.watchdog_interval_seconds),
            id="bama-slot-watchdog",
            name="Wall-clock slot watchdog",
            replace_existing=True,
        )

        log.info(
            "scheduler.started",
            instance=self.instance_id,
            timezone=self.cfg.timezone,
            run_at=self.cfg.run_at,
            next_fire=json.dumps(self.describe_next()),
            system_timezone=str(datetime.now().astimezone().tzinfo),
        )
        print(
            json.dumps(
                {
                    "event": "scheduler_started",
                    "instance_id": self.instance_id,
                    "pid": os.getpid(),
                    "timezone": self.cfg.timezone,
                    "run_at": self.cfg.run_at,
                    "next_trigger": self.describe_next(),
                    "database": _redact(self.cfg.database_url),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        self._reconcile_on_startup()
        self._install_signal_handlers()
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("scheduler.stopped")
        return 0

    def _scheduled_job(self) -> None:
        self.execute(trigger_type=TriggerType.SCHEDULED, scheduled_for=self.todays_slot())

    def _watchdog(self) -> None:
        """Re-decide from the wall clock, often, instead of trusting one long timer.

        APScheduler waits on ``threading.Event.wait(seconds)``. On macOS that
        timeout is measured by ``mach_absolute_time()``, which **stops while the
        machine is asleep** -- and Python exposes no ``CLOCK_BOOTTIME`` there to
        measure it any other way.

        The consequence was observed, not theorised. The daemon set one
        20.24-hour timer for the 13:00 slot. By the time that slot arrived the
        machine had accumulated 131 hours of sleep against 298 hours of wall
        clock, so the timer had barely advanced and the job did not fire -- not
        late, simply not that day. A daily schedule on a laptop degrades into
        "whenever the lid has been open long enough", silently.

        Polling cannot fix a clock, so this does not try. It keeps every waiting
        interval short enough that the drift is bounded by one interval, and makes
        the *decision* on the wall clock via :meth:`reconcile`, which is where the
        truth lives. The cron job is kept for machines that stay awake, where it
        fires precisely on the second; the two cannot double-run because the slot
        is unique per genuine run and the execution lock is held besides.
        """
        try:
            decision = self.reconcile()
        except Exception as exc:  # noqa: BLE001 - a watchdog must not die
            log.error("scheduler.watchdog_failed", error=repr(exc))
            return

        action = decision["action"]
        if action in ("none", "wait"):
            return

        log.info("scheduler.watchdog", **{k: str(v) for k, v in decision.items()})
        if action == "catch_up":
            late = float(decision.get("minutes_late") or 0.0)
            # Firing within a few minutes of the slot IS the scheduled run. Calling
            # it a catch-up would understate the automation; calling a two-hour-late
            # run `scheduled` would overstate it.
            trigger = (
                TriggerType.SCHEDULED
                if late <= self.cfg.on_time_grace_minutes
                else TriggerType.CATCH_UP
            )
            self.execute(trigger_type=trigger, scheduled_for=parse_ts(decision["slot"]))
        elif action == "record_missed":
            self.record_missed_slot(decision)

    def _reconcile_on_startup(self) -> None:
        decision = self.reconcile()
        log.info("scheduler.reconcile", **{k: str(v) for k, v in decision.items()})
        print(json.dumps({"event": "reconcile", **decision}, ensure_ascii=False), flush=True)

        if decision["action"] == "catch_up":
            slot = parse_ts(decision["slot"])
            outcome = self.execute(trigger_type=TriggerType.CATCH_UP, scheduled_for=slot)
            db = connect(self.cfg.database_url)
            try:
                Repository(db).record_missed_slot(
                    {
                        "production_schedule_name": self.cfg.production_schedule_name,
                        "scheduled_for": slot,
                        "scheduled_date_paris": (
                            slot.astimezone(self.zone).date().isoformat() if slot else None
                        ),
                        "detected_at": utcnow(),
                        "minutes_late": decision.get("minutes_late"),
                        "resolution": "caught_up",
                        "catch_up_run_id": outcome.get("run_id"),
                        "note": decision.get("reason"),
                    }
                )
            finally:
                db.close()
        elif decision["action"] == "record_missed":
            self.record_missed_slot(decision)

    def _install_signal_handlers(self) -> None:
        def handle(signum: int, _frame: Any) -> None:
            if self._stopping:
                return
            self._stopping = True
            log.info("scheduler.signal", signal=signum)
            if self.scheduler is not None:
                # wait=True: let an in-flight run finish rather than leaving a
                # half-applied comparison and a held lock behind.
                self.scheduler.shutdown(wait=True)

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, handle)


def _redact(url: str) -> str:
    if url.startswith(("postgres://", "postgresql://")):
        return "postgresql://<redacted>"
    return url


def _emit_status(payload: dict[str, Any]) -> None:
    """One machine-readable line on stdout; logs may precede it."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _run_due_cli(args: Any) -> int:
    """The portable one-shot entry point used by the external workflow."""
    checked_at = utcnow()
    if args.once or args.print_next:
        _emit_status(
            {
                "event": "run_due",
                "status": "config_error",
                "exit_code": 1,
                "checked_at_utc": checked_at.isoformat(),
                "reason": "--run-due cannot be combined with --once or --print-next",
            }
        )
        return 1
    if not args.config:
        _emit_status(
            {
                "event": "run_due",
                "status": "config_error",
                "exit_code": 1,
                "checked_at_utc": checked_at.isoformat(),
                "reason": "--run-due requires an explicit --config file",
            }
        )
        return 1
    if not args.slot_date:
        _emit_status(
            {
                "event": "run_due",
                "status": "config_error",
                "exit_code": 1,
                "checked_at_utc": checked_at.isoformat(),
                "reason": "--run-due requires --slot-date YYYY-MM-DD (the intended Paris date)",
            }
        )
        return 1
    slot_date: date | None = None
    date_error = ""
    try:
        candidate = date.fromisoformat(args.slot_date)
    except ValueError as exc:
        date_error = (
            f"--slot-date must be YYYY-MM-DD in the Paris calendar, got {args.slot_date!r}: {exc}"
        )
    else:
        if candidate.isoformat() != args.slot_date:
            date_error = f"--slot-date must be zero-padded YYYY-MM-DD, got {args.slot_date!r}"
        else:
            slot_date = candidate
    if slot_date is None:
        _emit_status(
            {
                "event": "run_due",
                "status": "config_error",
                "exit_code": 1,
                "checked_at_utc": checked_at.isoformat(),
                "reason": date_error,
            }
        )
        return 1

    try:
        cfg = load_config_strict(
            args.config,
            database_url=args.database_url,
            run_at=args.run_at,
            timezone=args.timezone,
            production_schedule_name=args.production_schedule_name,
        )
        validate_study_profile_paths(cfg)
    except Exception as exc:  # noqa: BLE001 - report every config failure as JSON
        _emit_status(
            {
                "event": "run_due",
                "status": "config_error",
                "exit_code": 1,
                "checked_at_utc": checked_at.isoformat(),
                "reason": str(exc),
            }
        )
        return 1

    cfg.ensure_dirs()
    daemon = SchedulerDaemon(cfg)
    try:
        result = daemon.run_due(slot_date=slot_date, now=checked_at)
    except Exception as exc:  # noqa: BLE001 - the workflow needs an exit code, not a traceback
        log.error("scheduler.run_due_failed", error=repr(exc))
        _emit_status(
            {
                "event": "run_due",
                "status": "failed",
                "exit_code": 1,
                "checked_at_utc": checked_at.isoformat(),
                "slot_date_paris": slot_date.isoformat(),
                "study_profile": cfg.study_profile,
                "production_schedule_name": cfg.production_schedule_name,
                "reason": repr(exc),
            }
        )
        return 1
    _emit_status(result)
    return int(result.get("exit_code", 1))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--run-at", default=None, help="HH:MM local to --timezone")
    parser.add_argument("--timezone", default=None)
    parser.add_argument("--production-schedule-name", default=None)
    parser.add_argument(
        "--once",
        action="store_true",
        help="reconcile and exit without installing the trigger (for testing)",
    )
    parser.add_argument(
        "--print-next",
        action="store_true",
        help="print the next trigger instant and exit",
    )
    parser.add_argument(
        "--run-due",
        action="store_true",
        help=(
            "one-shot: observe the slot named by --slot-date if it is due, publish "
            "the snapshot, and exit with a machine-readable status"
        ),
    )
    parser.add_argument(
        "--slot-date",
        default=None,
        help="YYYY-MM-DD Paris calendar date of the intended daily slot",
    )
    args = parser.parse_args(argv)

    if args.run_due:
        return _run_due_cli(args)

    cfg = load_config(
        args.config,
        database_url=args.database_url,
        run_at=args.run_at,
        timezone=args.timezone,
        production_schedule_name=args.production_schedule_name,
    )
    cfg.ensure_dirs()
    daemon = SchedulerDaemon(cfg)

    if args.print_next:
        print(json.dumps(daemon.describe_next(), ensure_ascii=False, indent=2))
        return 0
    if args.once:
        decision = daemon.reconcile()
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return 0
    return daemon.start()


if __name__ == "__main__":
    sys.exit(main())
