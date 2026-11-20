"""Exercise local manual publication and daily reconciliation without external requests."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from monitor_helpers import make_discovery

from bama_monitor import nationwide, scheduler_daemon
from bama_monitor.models import DetailResult, DetailVerdict
from bama_monitor.scrapers import StaticDetailScraper, StaticDiscoveryScraper


def test_manual_publication_is_isolated_and_blocks_overlapping_collection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy_db = tmp_path / "legacy.sqlite"
    monkeypatch.setenv("BAMA_MONITOR_DATABASE_URL", f"sqlite:///{legacy_db}")
    price = 1_000_000_001
    keys = [f"local-{i}" for i in range(55)]
    discovery = make_discovery(keys, prices=dict.fromkeys(keys, price))
    details = {
        card.canonical_url: DetailResult(
            card.platform_ad_id,
            card.canonical_url,
            ok=True,
            http_status=200,
            verdict=DetailVerdict.STILL_ACTIVE,
            fields={
                "body_type": "passenger_car",
                "year_text": "1400",
                "year_jalali": 1400,
                "price_type": "lumpsum",
                "price_display_amount_text": f"{price:,}",
                "price_display_unit_text": "تومان",
                "api_raw_json": {"price.type": "lumpsum", "price.price": f"{price:,}"},
            },
        )
        for card in discovery.cards
    }
    monkeypatch.setattr(
        nationwide.DailyRunner,
        "_default_discovery",
        lambda *_: StaticDiscoveryScraper([discovery]),
    )
    monkeypatch.setattr(
        nationwide.DailyRunner,
        "_default_detail_factory",
        lambda *_, **kw: StaticDetailScraper(details),
    )
    export_snapshot = nationwide.export_daily_snapshot

    def overlapping_export(db, cfg, run_id, **kwargs):
        # Run a second invocation after the collection lock has been released
        # but before this invocation writes the latest-valid pointer.
        with monkeypatch.context() as second:
            second.setattr(
                nationwide.DailyRunner,
                "run_daily",
                lambda *a, **kw: pytest.fail("Second collection started during publication"),
            )
            assert nationwide.main([]) == 1
        return export_snapshot(db, cfg, run_id, **kwargs)

    monkeypatch.setattr(nationwide, "export_daily_snapshot", overlapping_export)
    assert nationwide.main([]) == 0
    root = tmp_path / "bama-nationwide-data"
    snapshot = json.loads((root / "manual/snapshots/latest_valid.json").read_text())
    assert snapshot["advertisements"] == len(keys)
    assert snapshot["trigger_type"] == "manual"
    assert not snapshot["in_genuine_history"]
    assert not (root / "daily").exists() and not legacy_db.exists()
    with sqlite3.connect(root / "manual/monitor.sqlite") as db:
        assert db.execute("SELECT id FROM monitoring_runs").fetchall() == [(snapshot["run_id"],)]


def test_daily_restart_records_downtime_without_inventing_observations(tmp_path, monkeypatch):
    root = tmp_path / "custom"
    cfg = nationwide._config(root / "daily")
    cfg.ensure_dirs()
    now = datetime(2026, 11, 20, 21, tzinfo=UTC)
    monkeypatch.setattr(nationwide, "utcnow", lambda: now)
    monkeypatch.setattr(scheduler_daemon, "utcnow", lambda: now)
    run_due = nationwide.SchedulerDaemon.run_due
    stop = Event()

    def reconcile(daemon, *, slot_date, now):
        result = run_due(daemon, slot_date=slot_date, now=now)
        assert result["status"] == "missed"
        if slot_date == now.date():
            stop.set()
        return result

    monkeypatch.setattr(nationwide.SchedulerDaemon, "run_due", reconcile)
    assert nationwide._daily(cfg, stop) == 0
    now += timedelta(days=2)
    stop.clear()
    assert nationwide._daily(cfg, stop) == 0
    with sqlite3.connect(cfg.sqlite_path) as db:
        days = db.execute("SELECT scheduled_date_paris FROM missed_schedule_slots ORDER BY 1")
        assert [d for (d,) in days] == ["2026-11-20", "2026-11-21", "2026-11-22"]
        assert db.execute("SELECT count(*) FROM monitoring_runs").fetchone() == (0,)
    assert not (root / "manual").exists()


def test_manual_command_rejects_existing_scheduled_state(tmp_path):
    cfg = nationwide._config(tmp_path / "manual")
    cfg.ensure_dirs()
    nationwide.SchedulerDaemon(cfg).register_schedule()
    with pytest.raises(nationwide.ProfileMismatchError, match="scheduled or test history"):
        nationwide.main(["--state-dir", str(tmp_path)])
