"""The opt-in nationwide profile and its portable one-shot scheduler contract.

These tests exercise the command the external GitHub Actions workflow invokes:

    python -m bama_monitor.scheduler_daemon \
        --config /abs/repo/deploy/profiles/nationwide-passenger.yaml \
        --run-due --slot-date YYYY-MM-DD

Everything below is offline. Discovery and detail adapters are replaced with
deterministic fakes; the real DailyRunner, health gate, snapshot export, slot
uniqueness and scheduler decision logic still run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bama_monitor import scheduler_daemon as sd
from bama_monitor.config import (
    LEGACY_DEFAULT_DATABASE_PATH,
    LEGACY_PRODUCTION_SCHEDULE_NAME,
    MONITOR_VERSION,
    MonitorConfig,
    load_config,
    load_config_strict,
    validate_study_profile_paths,
)
from bama_monitor.db import parse_ts
from bama_monitor.locking import run_lock
from bama_monitor.models import DetailResult, DetailVerdict, DiscoveredCard, DiscoveryResult
from bama_monitor.repository import Repository
from bama_monitor.scheduler import slot_for_paris_date
from bama_monitor.scheduler_daemon import (
    RUN_DUE_LOCK_NAME,
    SchedulerDaemon,
)

PARIS = ZoneInfo("Europe/Paris")
REPO = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO / "deploy" / "profiles" / "nationwide-passenger.yaml"


# ---------------------------------------------------------------------------
# Deterministic, network-free adapters
# ---------------------------------------------------------------------------


def _fake_discovery(search_url: str, count: int, *, healthy: bool = True) -> DiscoveryResult:
    cards = [
        DiscoveredCard(
            platform_ad_id=f"nw-{index:03d}",
            canonical_url=f"https://bama.ir/car/detail-nw-{index:03d}-car-1398",
            position=index,
            page_number=0,
            card_title=f"nationwide car {index}",
            card_price_raw="2,000,000,000",
            card_price_normalized=2_000_000_000,
            card_year="1398",
            card_mileage_raw="40,000 km",
            card_mileage_normalized=40_000,
            card_location="تهران",
        )
        for index in range(count)
    ]
    return DiscoveryResult(
        search_url=search_url,
        cards=cards,
        termination_reason=(
            "api_exhausted_after_2_empty_pages" if healthy else "max_pages_reached"
        ),
        pages_fetched=3,
        pages_failed=0,
        initial_page_ok=True,
        reached_verified_end=healthy,
        stabilization_completed=healthy,
        blocked=False,
    )


class _FakeDiscoveryScraper:
    def __init__(self, cfg: MonitorConfig, evidence_dir: Path, *, count: int = 60, healthy=True):
        self.cfg = cfg
        self.evidence_dir = evidence_dir
        self.count = count
        self.healthy = healthy
        # The real adapter writes raw pages and a scratch database here; the fake
        # keeps the same directory contract without touching the network.
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    async def discover(self, search_url: str) -> DiscoveryResult:
        return _fake_discovery(search_url, self.count, healthy=self.healthy)


class _FakeDetailScraper:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self) -> _FakeDetailScraper:
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def scrape(self, advertisement_url: str) -> DetailResult:
        return DetailResult(
            platform_ad_id=advertisement_url.rsplit("/", 1)[-1],
            url=advertisement_url,
            ok=True,
            verdict=DetailVerdict.STILL_ACTIVE,
            http_status=200,
            fields={
                "title": "nationwide car",
                "price_text": "2,000,000,000",
                "price_toman": 2_000_000_000,
                "year_text": "1398",
                "seller_type": "dealer",
            },
            media=[],
            content_hash=hashlib.sha256(advertisement_url.encode("utf-8")).hexdigest(),
            parser_version="test",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _portable_config(
    tmp_path: Path,
    db,
    *,
    min_absolute_count: int = 1,
    schedule_name: str | None = None,
) -> MonitorConfig:
    base = load_config(PROFILE_PATH)
    data = base.model_dump()
    data.update(
        {
            "database_url": db.url,
            "output_dir": tmp_path / "evidence",
            "reports_dir": tmp_path / "reports",
            "daily_snapshot_dir": tmp_path / "snapshots",
            "lock_dir": tmp_path / "locks",
        }
    )
    if schedule_name is not None:
        data["production_schedule_name"] = schedule_name
    data["health"] = {**data["health"], "min_absolute_count": min_absolute_count}
    return MonitorConfig(**data)


def _daemon(cfg: MonitorConfig) -> SchedulerDaemon:
    return SchedulerDaemon(cfg)


def _on_time(daemon: SchedulerDaemon, slot_date: date) -> datetime:
    return daemon.slot_for_paris_date(slot_date) + timedelta(minutes=3)


def _last_json_line(output: str) -> dict:
    """Parse the last complete JSON object in captured output.

    ``--run-due`` prints one compact status line, while ``--once`` keeps its
    existing pretty-printed decision. Structured logs may appear before either.
    """
    text = output.strip()
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "{":
            continue
        try:
            value = json.loads(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise AssertionError(f"no JSON status object in output:\n{output}")


# ---------------------------------------------------------------------------
# Profile and configuration
# ---------------------------------------------------------------------------


class TestNationwideProfile:
    def test_the_portable_yaml_declares_the_intended_series(self) -> None:
        cfg = load_config(PROFILE_PATH)
        assert cfg.study_profile == "nationwide_passenger_v1"
        assert cfg.production_schedule_name == "bama-nationwide-passenger-v1-1300-paris"
        assert cfg.timezone == "Europe/Paris"
        assert cfg.run_at == "13:00"
        # All origins: the legacy prefix included `country=iranian`.
        assert "country=" not in cfg.search_url
        assert "body=passenger_car" in cfg.search_url
        assert "year=1397-2018," in cfg.search_url
        assert "price=1000000000" in cfg.search_url
        # Restored-state layout: relative to the process working directory.
        assert cfg.output_dir == Path("evidence")
        assert cfg.reports_dir == Path("reports")
        assert cfg.daily_snapshot_dir == Path("snapshots")
        assert cfg.lock_dir == Path("locks")

    def test_the_legacy_configuration_hash_is_unchanged_by_the_new_field(self) -> None:
        """Old runs must stay comparable: `legacy` is absent from the payload."""
        cfg = MonitorConfig()
        payload = {
            "search_url": cfg.search_url,
            "timezone": cfg.timezone,
            "removal_confirmation_misses": cfg.removal_confirmation_misses,
            "persistent_absence_days": cfg.persistent_absence_days,
            "health": cfg.health.model_dump(),
            "scoring": cfg.scoring.model_dump(),
            "repost": cfg.repost.model_dump(),
            "monitor_version": MONITOR_VERSION,
        }
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        assert cfg.configuration_hash() == expected
        assert "study_profile" not in payload

    def test_a_non_legacy_profile_changes_the_configuration_hash(self) -> None:
        legacy = MonitorConfig()
        nationwide = MonitorConfig(study_profile="nationwide_passenger_v1")
        assert legacy.configuration_hash() != nationwide.configuration_hash()

    def test_a_non_legacy_profile_refuses_the_legacy_default_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BAMA_MONITOR_DATABASE_URL", raising=False)
        cfg = MonitorConfig(
            study_profile="nationwide_passenger_v1",
            production_schedule_name="bama-nationwide-passenger-v1-1300-paris",
        )
        assert cfg.sqlite_path == LEGACY_DEFAULT_DATABASE_PATH
        with pytest.raises(Exception, match="legacy default database"):
            validate_study_profile_paths(cfg)

    def test_a_non_legacy_profile_refuses_the_legacy_schedule_name(self, tmp_path: Path) -> None:
        cfg = load_config(PROFILE_PATH).model_copy(
            update={
                "database_url": f"sqlite:///{tmp_path / 'state.sqlite'}",
                "production_schedule_name": LEGACY_PRODUCTION_SCHEDULE_NAME,
            }
        )
        with pytest.raises(Exception, match="legacy production schedule name"):
            validate_study_profile_paths(cfg)

    def test_strict_loading_rejects_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(Exception, match="does not exist"):
            load_config_strict(tmp_path / "missing.yaml")

    def test_strict_loading_rejects_a_non_mapping_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.yaml"
        path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
        with pytest.raises(Exception, match="YAML mapping"):
            load_config_strict(path)


# ---------------------------------------------------------------------------
# Slot date arithmetic
# ---------------------------------------------------------------------------


class TestPortableSlotDate:
    @pytest.mark.parametrize(
        ("day", "expected_utc_hour"),
        [
            (date(2026, 7, 27), "11:00"),
            (date(2026, 12, 1), "12:00"),
            # Europe/Paris springs forward on 2027-03-28.
            (date(2027, 3, 28), "11:00"),
            # ... and falls back on 2026-10-25.
            (date(2026, 10, 25), "12:00"),
        ],
    )
    def test_the_paris_wall_clock_is_fixed_across_dst(
        self, day: date, expected_utc_hour: str
    ) -> None:
        cfg = load_config(PROFILE_PATH)
        slot = slot_for_paris_date(cfg, day)
        assert slot.astimezone(PARIS).strftime("%H:%M") == "13:00"
        assert slot.strftime("%H:%M") == expected_utc_hour
        assert slot.date() == day

    def test_the_daemon_addresses_the_same_slot_by_date(self) -> None:
        cfg = load_config(PROFILE_PATH)
        daemon = _daemon(cfg)
        day = date(2027, 3, 28)
        assert daemon.slot_for_paris_date(day) == slot_for_paris_date(cfg, day)


# ---------------------------------------------------------------------------
# One-shot decisions and publication
# ---------------------------------------------------------------------------


class TestRunDueContract:
    def test_early_invocation_on_the_intended_day_noops(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        result = daemon.run_due(
            slot_date=slot_date,
            now=daemon.slot_for_paris_date(slot_date) - timedelta(minutes=30),
            db=db,
        )
        assert result["status"] == "not_due"
        assert result["exit_code"] == 0
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == 0

    def test_on_time_invocation_is_scheduled_and_publishes(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        slot = daemon.slot_for_paris_date(slot_date)
        result = daemon.run_due(
            slot_date=slot_date,
            now=slot + timedelta(minutes=3),
            db=db,
            discovery_scraper=_FakeDiscoveryScraper(cfg, tmp_path / "evidence"),
            detail_scraper_factory=_FakeDetailScraper,
        )
        assert result["status"] == "completed", result
        assert result["exit_code"] == 0
        assert result["trigger_type"] == "scheduled"
        assert result["run_status"] == "valid"

        run = Repository(db).get_run(int(result["run_id"]))
        assert run is not None
        assert run["trigger_type"] == "scheduled"
        assert parse_ts(run["scheduled_for"]) == slot
        assert parse_ts(run["started_at"]) != slot, "started_at must be the real time"
        assert result["run_times"]["started_at"] != slot.isoformat()
        assert result["run_times"]["finished_at"] is not None

        pointer = json.loads((tmp_path / "snapshots" / "latest_valid.json").read_text())
        assert pointer["run_id"] == result["run_id"]
        assert Path(result["snapshot"]["destination"]).is_dir()
        assert (Path(result["snapshot"]["destination"]) / "manifest.json").is_file()

        schedule = Repository(db).get_production_schedule(cfg.production_schedule_name)
        assert schedule is not None
        assert int(schedule["first_genuine_scheduled_run_id"]) == result["run_id"]

    def test_a_second_invocation_is_idempotent(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        now = _on_time(daemon, slot_date)
        first = daemon.run_due(
            slot_date=slot_date,
            now=now,
            db=db,
            discovery_scraper=_FakeDiscoveryScraper(cfg, tmp_path / "evidence"),
            detail_scraper_factory=_FakeDetailScraper,
        )
        assert first["status"] == "completed"
        before = db.scalar("SELECT COUNT(*) FROM monitoring_runs")
        first_started = first["run_times"]["started_at"]

        second = daemon.run_due(slot_date=slot_date, now=now + timedelta(minutes=5), db=db)
        assert second["status"] == "already_settled"
        assert second["exit_code"] == 0
        assert second["run_id"] == first["run_id"]
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == before
        assert second["run_times"]["started_at"] == first_started

    def test_late_inside_grace_is_an_honest_catch_up(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        slot = daemon.slot_for_paris_date(slot_date)
        result = daemon.run_due(
            slot_date=slot_date,
            now=slot + timedelta(minutes=90),
            db=db,
            discovery_scraper=_FakeDiscoveryScraper(cfg, tmp_path / "evidence"),
            detail_scraper_factory=_FakeDetailScraper,
        )
        assert result["status"] == "completed", result
        assert result["trigger_type"] == "catch_up"
        run = Repository(db).get_run(int(result["run_id"]))
        assert parse_ts(run["scheduled_for"]) == slot
        assert parse_ts(run["started_at"]) != slot

    def test_beyond_grace_records_the_original_slot_without_observations(
        self, tmp_path: Path, db
    ) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        slot = daemon.slot_for_paris_date(slot_date)
        result = daemon.run_due(
            slot_date=slot_date,
            now=slot + timedelta(minutes=300),
            db=db,
        )
        assert result["status"] == "missed"
        assert result["exit_code"] == 1
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == 0
        assert not (tmp_path / "snapshots" / "latest_valid.json").exists()
        missed = db.fetchone("SELECT * FROM missed_schedule_slots")
        assert missed is not None
        assert parse_ts(missed["scheduled_for"]) == slot
        assert missed["resolution"] == "outside_grace_window"

    def test_an_older_paris_date_is_a_missed_slot(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        old_date = date(2026, 11, 19)
        result = daemon.run_due(
            slot_date=old_date,
            now=daemon.slot_for_paris_date(date(2026, 11, 20)) + timedelta(minutes=5),
            db=db,
        )
        assert result["status"] == "missed"
        assert result["exit_code"] == 1
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == 0
        missed = db.fetchone("SELECT * FROM missed_schedule_slots")
        assert parse_ts(missed["scheduled_for"]) == daemon.slot_for_paris_date(old_date)

    @pytest.mark.parametrize("previous_status", ["invalid", "failed", "partial"])
    def test_a_previous_failed_run_is_not_reported_as_success(
        self, tmp_path: Path, db, previous_status: str
    ) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        slot = daemon.slot_for_paris_date(slot_date)
        run_id, _ = Repository(db).create_or_get_run(
            search_url=cfg.search_url,
            scheduled_for=slot,
            timezone=cfg.timezone,
            configuration_hash=cfg.configuration_hash(),
            scraper_version="test",
            monitor_version="test",
            previous_valid_run_id=None,
            trigger_type="scheduled",
            production_schedule_name=cfg.production_schedule_name,
        )
        Repository(db).update_run(run_id, status=previous_status, finished_at=datetime.now(UTC))

        result = daemon.run_due(
            slot_date=slot_date,
            now=slot + timedelta(minutes=3),
            db=db,
            discovery_scraper=_FakeDiscoveryScraper(cfg, tmp_path / "evidence"),
            detail_scraper_factory=_FakeDetailScraper,
        )
        assert result["status"] == "previous_run_failed"
        assert result["exit_code"] == 1
        assert result["run_id"] == run_id
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == 1, "no second genuine row"

    def test_a_valid_but_unpublished_run_is_a_visible_failure(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        slot = daemon.slot_for_paris_date(slot_date)
        run_id, _ = Repository(db).create_or_get_run(
            search_url=cfg.search_url,
            scheduled_for=slot,
            timezone=cfg.timezone,
            configuration_hash=cfg.configuration_hash(),
            scraper_version="test",
            monitor_version="test",
            previous_valid_run_id=None,
            trigger_type="scheduled",
            production_schedule_name=cfg.production_schedule_name,
        )
        Repository(db).update_run(run_id, status="valid", finished_at=datetime.now(UTC))

        result = daemon.run_due(slot_date=slot_date, now=slot + timedelta(minutes=3), db=db)
        assert result["status"] == "previous_run_unpublished"
        assert result["exit_code"] == 1
        assert result["run_id"] == run_id
        assert not (tmp_path / "snapshots" / "latest_valid.json").exists()

    def test_an_invalid_fresh_run_publishes_nothing(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        result = daemon.run_due(
            slot_date=slot_date,
            now=_on_time(daemon, slot_date),
            db=db,
            discovery_scraper=_FakeDiscoveryScraper(cfg, tmp_path / "evidence", healthy=False),
            detail_scraper_factory=_FakeDetailScraper,
        )
        assert result["status"] == "invalid"
        assert result["exit_code"] == 1
        assert result["run_status"] != "valid"
        assert not (tmp_path / "snapshots" / "latest_valid.json").exists()
        run = Repository(db).get_run(int(result["run_id"]))
        assert run["snapshot_export_path"] is None

    def test_a_concurrent_invocation_is_refused_by_the_oneshot_lock(
        self, tmp_path: Path, db
    ) -> None:
        cfg = _portable_config(tmp_path, db)
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        from bama_monitor.db import connect

        other = connect(cfg.database_url)
        try:
            with run_lock(other, cfg.lock_dir, name=RUN_DUE_LOCK_NAME):
                result = daemon.run_due(
                    slot_date=slot_date,
                    now=_on_time(daemon, slot_date),
                    db=db,
                    discovery_scraper=_FakeDiscoveryScraper(cfg, tmp_path / "evidence"),
                    detail_scraper_factory=_FakeDetailScraper,
                )
        finally:
            other.close()
        assert result["status"] == "locked"
        assert result["exit_code"] == 1
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == 0

    def test_a_profile_mismatch_refuses_to_mix_histories(self, tmp_path: Path, db) -> None:
        cfg = _portable_config(tmp_path, db)
        Repository(db).upsert_production_schedule(
            {
                "name": cfg.production_schedule_name,
                "search_configuration_hash": "a-different-series",
                "timezone": cfg.timezone,
                "run_at_local": cfg.run_at,
            }
        )
        daemon = _daemon(cfg)
        slot_date = date(2026, 11, 20)
        result = daemon.run_due(
            slot_date=slot_date,
            now=_on_time(daemon, slot_date),
            db=db,
        )
        assert result["status"] == "profile_error"
        assert result["exit_code"] == 1
        assert db.scalar("SELECT COUNT(*) FROM monitoring_runs") == 0


# ---------------------------------------------------------------------------
# The actual command-line contract, from a restored state directory
# ---------------------------------------------------------------------------


class TestPortableCliContract:
    def test_run_due_from_a_restored_state_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        state = tmp_path / "restored-state"
        state.mkdir()
        monkeypatch.chdir(state)
        monkeypatch.setenv("BAMA_MONITOR_DATABASE_URL", f"sqlite:///{state / 'monitor.sqlite'}")
        # Freeze the decision clock at 13:05 Paris on the intended date.
        fixed_now = datetime(2026, 11, 20, 12, 5, tzinfo=UTC)
        monkeypatch.setattr(sd, "utcnow", lambda: fixed_now)
        monkeypatch.setattr("bama_monitor.scrapers.BamaDiscoveryScraper", _FakeDiscoveryScraper)
        monkeypatch.setattr("bama_monitor.scrapers.BamaDetailScraper", _FakeDetailScraper)
        capsys.readouterr()

        code = sd.main(
            [
                "--config",
                str(PROFILE_PATH),
                "--run-due",
                "--slot-date",
                "2026-11-20",
            ]
        )
        output = capsys.readouterr().out
        payload = _last_json_line(output)

        assert code == 0, (code, output, payload)
        assert payload["event"] == "run_due"
        assert payload["status"] == "completed"
        assert payload["trigger_type"] == "scheduled"
        assert payload["study_profile"] == "nationwide_passenger_v1"
        assert payload["production_schedule_name"] == ("bama-nationwide-passenger-v1-1300-paris")
        assert payload["slot_utc"] == "2026-11-20T12:00:00+00:00"

        # The contract's state layout is relative to the restored cwd.
        assert (state / "monitor.sqlite").is_file()
        assert (state / "evidence" / "runs" / f"run_{payload['run_id']}").is_dir()
        assert (state / "reports").is_dir()
        assert (state / "locks").is_dir()
        assert list((state / "snapshots").glob("2026-11-20/*/run_*/manifest.json"))
        pointer = json.loads((state / "snapshots" / "latest_valid.json").read_text())
        assert pointer["run_id"] == payload["run_id"]
        assert pointer["scheduled_for"] == "2026-11-20T12:00:00+00:00"
        assert pointer["in_genuine_history"] is True

        # A second invocation returns the same settled slot.
        capsys.readouterr()
        code_again = sd.main(
            [
                "--config",
                str(PROFILE_PATH),
                "--run-due",
                "--slot-date",
                "2026-11-20",
            ]
        )
        again = _last_json_line(capsys.readouterr().out)
        assert code_again == 0
        assert again["status"] == "already_settled"
        assert again["run_id"] == payload["run_id"]

    def test_run_due_rejects_a_missing_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code = sd.main(
            [
                "--config",
                str(tmp_path / "missing.yaml"),
                "--run-due",
                "--slot-date",
                "2026-11-20",
            ]
        )
        payload = _last_json_line(capsys.readouterr().out)
        assert code == 1
        assert payload["status"] == "config_error"
        assert "does not exist" in payload["reason"]

    def test_run_due_rejects_a_non_date_slot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code = sd.main(
            [
                "--config",
                str(PROFILE_PATH),
                "--run-due",
                "--slot-date",
                "20-11-2026",
            ]
        )
        payload = _last_json_line(capsys.readouterr().out)
        assert code == 1
        assert payload["status"] == "config_error"
        assert "YYYY-MM-DD" in payload["reason"]

    def test_run_due_requires_a_config_and_slot_date(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code = sd.main(["--run-due", "--slot-date", "2026-11-20"])
        assert code == 1
        assert _last_json_line(capsys.readouterr().out)["status"] == "config_error"

    def test_once_remains_inspect_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BAMA_MONITOR_DATABASE_URL", f"sqlite:///{tmp_path / 'inspect.sqlite'}")
        code = sd.main(
            [
                "--config",
                str(PROFILE_PATH),
                "--once",
            ]
        )
        payload = _last_json_line(capsys.readouterr().out)
        assert code == 0
        assert "action" in payload
        assert "status" not in payload, "--once keeps its reconcile-decision shape"
        assert not (tmp_path / "snapshots" / "latest_valid.json").exists()
