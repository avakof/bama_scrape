"""Offline multi-day census with today's card and detail evidence."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from monitor_helpers import BASE_TIME, make_card, make_discovery

from bama_monitor.config import MonitorConfig
from bama_monitor.daily_snapshot import build_daily_dataset, export_daily_snapshot, validate_run
from bama_monitor.db import Database, connect
from bama_monitor.models import DetailResult, DetailVerdict, DiscoveryResult, EventType, RunHealth
from bama_monitor.repository import Repository
from bama_monitor.runner import DailyRunner, RunOutcome

FLOOR = 1_000_000_000
ABOVE = FLOOR + 50_000_000
PROFILE = "nationwide_passenger_v1"


class Discovery:
    def __init__(self, result: DiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    async def discover(self, url: str) -> DiscoveryResult:
        self.calls += 1
        return self.result


class Details:
    def __init__(self, entries: dict[str, DetailResult]) -> None:
        self.entries = {entry.url: entry for entry in entries.values()}
        self.calls: list[str] = []

    @asynccontextmanager
    async def factory(self):
        yield self

    async def scrape(self, url: str) -> DetailResult:
        self.calls.append(url)
        return self.entries[url]


def detail(
    ad_id: str,
    price: int | None = ABOVE,
    *,
    verdict: DetailVerdict = DetailVerdict.STILL_ACTIVE,
    **fields: Any,
) -> DetailResult:
    return DetailResult(
        platform_ad_id=ad_id,
        url=make_card(ad_id).canonical_url,
        ok=verdict is DetailVerdict.STILL_ACTIVE,
        verdict=verdict,
        http_status=410 if verdict is DetailVerdict.HTTP_410 else 200,
        # A constant adapter hash deliberately exercises strict price hashing.
        content_hash="fixture-content",
        fields={
            "title": f"car {ad_id}",
            "body_type": "passenger_car",
            "year_text": "1400",
            "year_jalali": 1400,
            "price_text": f"{price:,}" if price else None,
            "price_display_amount_text": f"{price:,}" if price else None,
            "price_display_unit_text": "تومان" if price else None,
            "api_raw_json": (
                {"price.type": "lumpsum", "price.price": f"{price:,}"} if price else {}
            ),
            "price_type": "lumpsum",
            "is_pre_sale": False,
            "price_toman": price,
            **fields,
        },
    )


@pytest.fixture
def study(cfg: MonitorConfig) -> MonitorConfig:
    return MonitorConfig.model_validate(
        cfg.model_dump()
        | {
            "study_profile": PROFILE,
            "production_schedule_name": "bama-nationwide-passenger-v1-1300-paris",
            "daily_snapshot_dir": cfg.output_dir / "snapshots",
            "health": {"min_absolute_count": 1, "max_missing_ratio": 1.0},
            "repost": {"enabled": False},
        }
    )


async def run(
    cfg: MonitorConfig,
    day: int,
    cards: dict[str, int],
    entries: dict[str, DetailResult],
    *,
    healthy: bool = True,
) -> tuple[RunOutcome, Discovery, Details]:
    discovery = Discovery(make_discovery(list(cards), prices=cards, healthy=healthy))
    details = Details(entries)
    runner = DailyRunner(cfg, discovery_scraper=discovery, detail_scraper_factory=details.factory)
    try:
        outcome = await runner.run_daily(
            scheduled_for=BASE_TIME + timedelta(days=day),
            trigger_type="scheduled",
            production_schedule_name=cfg.production_schedule_name,
        )
    finally:
        runner.close()
    return outcome, discovery, details


def rows(db: Database, outcome: RunOutcome) -> dict[str, dict[str, Any]]:
    assert outcome.run_id is not None
    return {
        str(row["platform_ad_id"]): row
        for row in db.fetchall(
            "SELECT o.*, a.platform_ad_id FROM daily_ad_observations o"
            " JOIN advertisements a ON a.id=o.advertisement_id WHERE o.run_id=?",
            [outcome.run_id],
        )
    }


@pytest.mark.asyncio
async def test_nationwide_longitudinal(study: MonitorConfig) -> None:
    first, _, first_details = await run(
        study,
        1,
        {"present": ABOVE, "absent": ABOVE, "gone": ABOVE, "anchor": ABOVE, "deposit": ABOVE},
        {
            "present": detail("present"),
            "absent": detail("absent"),
            "gone": detail("gone"),
            "anchor": detail("anchor", seller_type="dealer"),
            "deposit": detail("deposit", down_payment_toman=ABOVE, is_installment=True),
        },
    )
    assert first.status is RunHealth.VALID and first.comparison is not None
    assert first.comparison.seen_ids == ["present", "absent", "gone", "anchor"]
    assert first.detail_accounted == 4
    assert len(first_details.calls) == len(set(first_details.calls)) == 5

    second, _, second_details = await run(
        study,
        2,
        {"present": FLOOR, "anchor": ABOVE},
        {
            "present": detail("present", FLOOR),
            "absent": detail("absent", FLOOR - 1),
            "gone": detail("gone", verdict=DetailVerdict.HTTP_410),
            "anchor": detail("anchor"),
        },
    )
    assert second.status is RunHealth.VALID and second.comparison is not None
    assert second.comparison.seen_ids == ["anchor"]
    assert second.comparison.missing_ids == ["gone"]
    assert second.comparison.likely_removed_ids == []
    assert second.filter_exits == 2
    assert len(second_details.calls) == len(set(second_details.calls)) == 4

    third, _, _ = await run(
        study,
        3,
        {"present": ABOVE, "absent": ABOVE, "anchor": ABOVE},
        {
            "present": detail("present", ABOVE + 1),
            "absent": detail("absent", ABOVE),
            "anchor": detail("anchor"),
            "gone": detail("gone", verdict=DetailVerdict.HTTP_410),
        },
    )
    assert third.comparison is not None
    assert third.comparison.likely_removed_ids == ["gone"]

    replay, replay_discovery, replay_details = await run(study, 2, {}, {})
    assert replay.run_id == second.run_id
    assert replay.status is second.status
    assert replay.comparison and replay.comparison.counters() == second.comparison.counters()
    assert replay_discovery.calls == 0 and not replay_details.calls

    db = connect(study.database_url)
    try:
        repo = Repository(db)
        assert first.run_id is not None and second.run_id is not None and third.run_id is not None
        assert rows(db, first)["present"]["eligible_price_toman"] == ABOVE
        assert rows(db, third)["present"]["eligible_price_toman"] == ABOVE + 1
        assert not rows(db, first)["deposit"]["was_seen"]
        assert rows(db, first)["deposit"]["raw_card_price_normalized"] == ABOVE
        assert rows(db, first)["deposit"]["card_price_normalized"] is None
        assert repo.get_run(first.run_id)["active_count"] == 4
        assert repo.get_run(first.run_id)["candidate_count"] == 5
        assert rows(db, second)["present"]["candidate_present"]
        assert not rows(db, second)["absent"]["candidate_present"]
        assert rows(db, second)["present"]["assessed_price_toman"] == FLOOR
        assert rows(db, second)["absent"]["assessed_price_toman"] == FLOOR - 1
        assert not rows(db, second)["present"]["was_seen"]
        assert not rows(db, second)["absent"]["was_seen"]
        assert repo.get_advertisement("deposit")["total_seen_runs"] == 0
        assert repo.get_advertisement("present")["total_missing_runs"] == 0
        assert repo.get_advertisement("absent")["total_missing_runs"] == 0
        assert {
            row["platform_ad_id"]
            for row in repo.events_for_run(third.run_id, EventType.FILTER_REENTRY)
        } == {"present", "absent"}
        assert not repo.events_for_run(second.run_id, EventType.REMOVAL_CONFIRMED)
        assert validate_run(db, first.run_id).valid
        assert validate_run(db, second.run_id).valid
        assert export_daily_snapshot(db, study, second.run_id)["exported"]
        dataset = build_daily_dataset(db, second.run_id).set_index("advertisement_id")
        assert dataset.loc["present", "current_status"] == "active_outside_filter"
        assert (
            dataset.loc["present", "price_normalized"] is dataset.loc["absent", "price_normalized"]
        )
        assert dataset["price_normalized"].isna().sum() == 3
        assert int(dataset.loc["anchor", "price_normalized"]) == ABOVE
        changes = repo.price_changes_for_run(third.run_id)
        assert [(change["old_price"], change["new_price"]) for change in changes] == [
            (ABOVE, ABOVE + 1)
        ]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_unknown_current_detail_and_invalid_run_do_not_create_a_disappearance(
    study: MonitorConfig,
) -> None:
    anchor = detail("anchor")
    await run(
        study,
        1,
        {"anchor": ABOVE, "tracked": ABOVE, "missing": ABOVE},
        {"anchor": anchor, "tracked": detail("tracked"), "missing": detail("missing")},
    )
    invalid, _, invalid_details = await run(
        study, 2, {"tracked": ABOVE}, {"tracked": detail("tracked", FLOOR)}, healthy=False
    )
    assert invalid.status is RunHealth.PARTIAL and invalid.comparison is not None
    assert not invalid.comparison.applied and not invalid_details.calls

    unresolved, _, _ = await run(
        study,
        3,
        {"anchor": ABOVE, "tracked": FLOOR},
        {
            "anchor": anchor,
            # The current card's permissive normalized value is the exact floor.
            # A live page lacking supported price evidence leaves the result unknown.
            "tracked": detail("tracked", FLOOR, price_text="ambiguous 1,000,000,000"),
            "missing": detail("missing", None, price_type=None),
        },
    )
    assert unresolved.status is RunHealth.VALID and unresolved.comparison is not None
    assert unresolved.detail_coverage_rate == 0.5
    assert unresolved.filter_exits == 0
    assert unresolved.comparison.missing_ids == []
    assert unresolved.unexplained_absences == 1

    db = connect(study.database_url)
    try:
        assert invalid.run_id is not None and unresolved.run_id is not None
        assert not rows(db, invalid)["tracked"]["was_seen"]
        assert not export_daily_snapshot(db, study, invalid.run_id)["exported"]
        today = rows(db, unresolved)
        assert today["tracked"]["candidate_present"]
        assert not today["tracked"]["was_seen"]
        assert today["tracked"]["eligibility_status"] == "unknown"
        assert today["missing"]["eligibility_status"] == "unknown"
        assert today["tracked"]["eligible_price_toman"] is None
        assert today["tracked"]["card_price_normalized"] is None
        assert validate_run(db, unresolved.run_id).valid
        assert export_daily_snapshot(db, study, unresolved.run_id)["exported"]
        repo = Repository(db)
        for ad_id in ("tracked", "missing"):
            row = repo.get_advertisement(ad_id)
            assert row and row["total_missing_runs"] == 0 and row["total_seen_runs"] == 1
            assert row["sale_label"] == "unknown"
        assert not repo.events_for_run(unresolved.run_id, EventType.LIKELY_SOLD)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_study_detail_budget_and_block(study: MonitorConfig) -> None:
    study.detail.check_every_advertisement_daily = False
    study.detail.max_details_per_run = 1
    capped, _, capped_details = await run(
        study, 1, {"first": ABOVE, "unchecked": ABOVE}, {"first": detail("first")}
    )
    assert capped.status is RunHealth.VALID and capped.comparison is not None
    assert capped.comparison.seen_ids == ["first"]
    assert len(capped_details.calls) == 1
    assert capped.detail_coverage_rate == 0.5

    study.detail.check_every_advertisement_daily = True
    blocked, _, blocked_details = await run(
        study,
        2,
        {"blocked": ABOVE, "next": ABOVE},
        {"blocked": detail("blocked", verdict=DetailVerdict.BLOCKED)},
    )
    assert blocked.status is RunHealth.BLOCKED
    assert blocked.comparison and not blocked.comparison.applied
    assert len(blocked_details.calls) == 1


@pytest.mark.asyncio
async def test_legacy_membership_is_unchanged(cfg: MonitorConfig) -> None:
    outcome, _, _ = await run(cfg, 1, {"exact": FLOOR}, {"exact": detail("exact", FLOOR)})
    assert outcome.comparison and outcome.comparison.seen_ids == ["exact"]
    db = connect(cfg.database_url)
    try:
        assert outcome.run_id is not None
        assert rows(db, outcome)["exact"]["card_price_normalized"] == FLOOR
        assert "eligibility_status" not in build_daily_dataset(db, outcome.run_id)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_bare_cash_card_is_resolved_from_todays_detail_display(study: MonitorConfig) -> None:
    """The card and detail API have no unit; only today's display can prove one."""
    bare = f"{ABOVE:,}"
    fields = {
        "price_text": bare,
        "api_raw_json": {
            "detail.body_type": "passenger_car",
            "detail.year": "1400",
            "detail.is_pre_sale": False,
            "price.type": "lumpsum",
            "price.price": bare,
        },
        "price_display_amount_text": bare,
        "price_display_unit_text": "تومان",
    }
    first, _, checks = await run(study, 1, {"cash": ABOVE}, {"cash": detail("cash", **fields)})
    assert first.comparison and first.comparison.seen_ids == ["cash"]
    assert len(checks.calls) == 1

    second, _, _ = await run(
        study,
        2,
        {"cash": ABOVE},
        {
            "cash": detail(
                "cash",
                **(fields | {"price_display_amount_text": None, "price_display_unit_text": None}),
            )
        },
    )
    assert second.status is RunHealth.INVALID
    assert second.comparison and not second.comparison.applied
    assert second.comparison.missing_ids == []
    assert second.detail_coverage_rate == 0

    db = connect(study.database_url)
    try:
        first_row = rows(db, first)["cash"]
        second_row = rows(db, second)["cash"]
        assert first_row["eligibility_source"] == "live_detail"
        assert first_row["eligible_price_toman"] == ABOVE
        assert second_row["eligibility_reason"] == "price_unit_unproven"
        assert second_row["eligible_price_toman"] is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_mixed_cash_and_unresolved_candidates_publish_verified_cohort(
    study: MonitorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from bama_deep import net

    from bama_monitor.scrapers import BamaDetailScraper

    keys = ("cash01", "neg02", "amb03")
    calls: list[tuple[str, str]] = []

    class LocalDetailFetcher:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: Any) -> None:
            pass

        async def fetch_json(self, url: str) -> dict[str, Any]:
            key = next(key for key in keys if key in url)
            calls.append(("api", key))
            price = (
                {"type": "negotiable", "price": "0"}
                if key == "neg02"
                else {"type": "lumpsum", "price": f"{ABOVE:,}"}
            )
            return {
                "data": {
                    "detail": {
                        "code": key,
                        "title": "Synthetic passenger car",
                        "body_type": "passenger_car",
                        "year": "1400",
                        "is_pre_sale": False,
                    },
                    "price": price,
                }
            }

        async def fetch_html(self, url: str) -> str:
            key = next(key for key in keys if key in url)
            calls.append(("html", key))
            schema = {"offers": {"priceCurrency": "IRR"}}
            if key != "neg02":
                schema["offers"]["price"] = str(ABOVE)
            display = (
                f'<div class="flex items-end self-stretch"><div class="flex flex-col">'
                f'<div class="flex items-center"><span>{ABOVE:,}</span><span>تومان</span>'
                "</div></div></div>"
                if key == "cash01"
                else ""
            )
            return f'<script type="application/ld+json">{json.dumps(schema)}</script>{display}'

    monkeypatch.setattr(net, "DeepFetcher", lambda _: LocalDetailFetcher())
    discovery = Discovery(make_discovery(list(keys), prices=dict.fromkeys(keys, ABOVE)))
    runner = DailyRunner(
        study, discovery_scraper=discovery, detail_scraper_factory=lambda: BamaDetailScraper(study)
    )
    try:
        outcome = await runner.run_daily(scheduled_for=BASE_TIME)
        assert outcome.run_id is not None
        assert outcome.status is RunHealth.VALID
        assert outcome.comparison and outcome.comparison.seen_ids == ["cash01"]
        assert outcome.eligibility_counts == {"qualified": 1, "unknown": 2}
        assert outcome.detail_coverage_rate == pytest.approx(1 / 3)
        assert sorted(calls) == [
            (surface, key) for surface in ("api", "html") for key in sorted(keys)
        ]

        result = export_daily_snapshot(runner.db, study, outcome.run_id)
        assert result["exported"]
        from pathlib import Path

        summary = json.loads((Path(result["destination"]) / "run_summary.json").read_text())
        assert summary["candidate_count"] == 3
        assert summary["counters"]["active_count"] == 1
        assert summary["candidate_eligibility_counts"] == {"qualified": 1, "unknown": 2}
        excluded = [
            row for row in summary["eligibility_reasons"] if row["eligibility_status"] == "unknown"
        ]
        assert len(excluded) == 2
        assert all(row["eligibility_reason"] and row["count"] == 1 for row in excluded)
    finally:
        runner.close()


@pytest.mark.asyncio
async def test_systemic_detail_failure_vetoes_removal(study: MonitorConfig) -> None:
    await run(
        study,
        1,
        {"anchor": ABOVE, "missing": ABOVE},
        {"anchor": detail("anchor"), "missing": detail("missing")},
    )
    outcome, _, _ = await run(
        study,
        2,
        {"anchor": ABOVE, "failed1": ABOVE, "failed2": ABOVE},
        {
            "anchor": detail("anchor"),
            "failed1": detail("failed1", verdict=DetailVerdict.PARSE_ERROR),
            "failed2": detail("failed2", verdict=DetailVerdict.TEMPORARY_ERROR),
            "missing": detail("missing", verdict=DetailVerdict.HTTP_410),
        },
    )
    assert outcome.status is RunHealth.INVALID
    assert outcome.health and "detail_infrastructure_available" in outcome.health.reason()
    assert outcome.comparison and not outcome.comparison.applied
    db = connect(study.database_url)
    try:
        assert outcome.run_id is not None
        assert not export_daily_snapshot(db, study, outcome.run_id)["exported"]
        row = Repository(db).get_advertisement("missing")
        assert row and row["total_missing_runs"] == 0
        assert rows(db, outcome)["missing"]["eligibility_status"] == "missing"
    finally:
        db.close()
