"""The daily comparison workflow.

Order matters and is enforced here:

1. persist the discovered inventory (evidence first — it survives any later failure);
2. validate run health;
3. **only if valid**, open a transaction, apply state transitions, write events,
   update counters, commit.

If any step in the transaction fails, the whole set of transitions rolls back. A
half-applied comparison would leave some advertisements with an incremented miss
counter and others not, and nothing downstream could tell which — so partial
application is treated as unacceptable rather than merely unfortunate.

An invalid run still persists everything it observed. It simply does not get to
change any advertisement's status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import MonitorConfig
from .db import Database, parse_ts, utcnow
from .models import (
    AdStatus,
    ComparisonResult,
    DetailAvailability,
    DiscoveredCard,
    DiscoveryResult,
    EventType,
    FilterExitReason,
    RunContext,
    RunHealth,
    StatusTransition,
)
from .repository import Repository
from .run_health import HealthReport, evaluate_run, recent_valid_counts
from .state_machine import AdState, on_missing, on_seen


@dataclass
class PersistOutcome:
    """Result of writing the discovered inventory."""

    seen_ids: list[str]
    new_ids: list[str]
    ad_ids: dict[str, int]
    price_changes: int
    persisted: bool
    error: str | None = None


@dataclass
class StudyObservation:
    """Evidence for one candidate or previously qualified listing."""

    platform_ad_id: str
    url: str
    card: DiscoveredCard | None = None
    status: str = "unknown"
    reason: str = "detail_not_checked"
    source: str = "none"
    price_toman: int | None = None


class InventoryComparator:
    """Compares a run's inventory against accumulated state."""

    def __init__(self, db: Database, cfg: MonitorConfig) -> None:
        self.db = db
        self.cfg = cfg
        self.repo = Repository(db)

    # -- step 2: persist ---------------------------------------------------

    def persist_inventory(self, result: DiscoveryResult, context: RunContext) -> PersistOutcome:
        """Upsert every discovered advertisement and record its observation.

        Runs *before* health validation on purpose: whatever the scraper managed to
        see is evidence worth keeping even if the run is later judged unusable.
        No status transitions happen here.
        """
        seen_ids: list[str] = []
        new_ids: list[str] = []
        ad_ids: dict[str, int] = {}
        price_changes = 0

        unique, duplicates = self.unique_cards(result)

        try:
            for platform_ad_id, card in unique.items():
                existing = self.repo.get_advertisement(platform_ad_id)
                is_new = existing is None
                extra: dict[str, Any] = {"search_configuration_hash": context.configuration_hash}
                if is_new:
                    extra["current_status"] = AdStatus.NEW
                    extra["first_run_id"] = context.run_id
                ad_id = self.repo.upsert_advertisement(
                    platform_ad_id=platform_ad_id,
                    canonical_url=card.canonical_url,
                    **extra,
                )
                ad_ids[platform_ad_id] = ad_id
                seen_ids.append(platform_ad_id)
                if is_new:
                    new_ids.append(platform_ad_id)

                self.repo.record_observation(
                    run_id=context.run_id,
                    advertisement_id=ad_id,
                    was_seen=True,
                    observed_at=context.started_at,
                    card=card,
                )
                if not is_new:
                    price_changes += self._detect_card_price_change(ad_id, card, context)
        except Exception as exc:  # noqa: BLE001
            return PersistOutcome(
                seen_ids, new_ids, ad_ids, price_changes, persisted=False, error=repr(exc)
            )

        self.repo.update_run(
            context.run_id,
            discovered_count=len(unique),
            duplicate_count=duplicates,
            termination_reason=result.termination_reason,
        )
        return PersistOutcome(seen_ids, new_ids, ad_ids, price_changes, persisted=True)

    @staticmethod
    def unique_cards(result: DiscoveryResult) -> tuple[dict[str, DiscoveredCard], int]:
        # Deduplicate: platform id first, then canonical URL as a fallback.
        unique: dict[str, DiscoveredCard] = {}
        by_url: dict[str, str] = {}
        duplicates = 0
        for card in result.cards:
            key = card.platform_ad_id
            if key in unique:
                duplicates += 1
                continue
            if card.canonical_url in by_url:
                duplicates += 1
                continue
            unique[key] = card
            by_url[card.canonical_url] = key

        return unique, duplicates

    def _detect_card_price_change(
        self, advertisement_id: int, card: DiscoveredCard, context: RunContext
    ) -> int:
        """Compare this card's price with the previous *seen* observation.

        Card-level detection is cheap and catches the change on the day it happens;
        the detail scraper then confirms it. Hidden/revealed prices are recorded as
        their own change types rather than as a numeric move.
        """
        previous = self.repo.previous_seen_observation(advertisement_id, context.run_id)
        if not previous:
            return 0
        old_price = previous.get("card_price_normalized")
        new_price = card.card_price_normalized
        old_raw = previous.get("card_price_raw")
        new_raw = card.card_price_raw

        change_type: str | None = None
        if old_price is not None and new_price is not None and old_price != new_price:
            change_type = "price_increase" if new_price > old_price else "price_decrease"
        elif old_price is not None and new_price is None:
            change_type = "price_hidden"
        elif old_price is None and new_price is not None:
            change_type = "price_revealed"

        if change_type is None:
            return 0

        self.repo.record_price_change(
            advertisement_id=advertisement_id,
            run_id=context.run_id,
            change_type=change_type,
            old_price=old_price,
            new_price=new_price,
            old_price_raw=old_raw,
            new_price_raw=new_raw,
            changed_at=context.started_at,
        )
        self.repo.record_event(
            advertisement_id=advertisement_id,
            run_id=context.run_id,
            event_type=EventType.PRICE_CHANGED,
            previous_status=None,
            new_status=None,
            event_at=context.started_at,
            evidence={
                "change_type": change_type,
                "old_price": old_price,
                "new_price": new_price,
                "old_price_raw": old_raw,
                "new_price_raw": new_raw,
                "source": "search_card",
            },
        )
        return 1

    # -- step 3: health ----------------------------------------------------

    def validate(
        self,
        result: DiscoveryResult,
        context: RunContext,
        *,
        persisted: bool,
        study_misses: set[str] | None = None,
    ) -> HealthReport:
        """Evaluate whether this run may drive status transitions."""
        baseline = recent_valid_counts(
            self.db, context.configuration_hash, self.cfg.health.median_window_runs
        )
        study = study_misses is not None
        population = self.repo.comparison_population(context.configuration_hash, study=study)
        if study_misses is None:
            seen = result.unique_ids()
            expected_missing = sum(
                1 for row in population if str(row["platform_ad_id"]) not in seen
            )
        else:
            expected_missing = len(study_misses)
        return evaluate_run(
            result,
            self.cfg,
            baseline_counts=baseline,
            known_active_count=len(population) or None,
            expected_missing_count=expected_missing,
            persisted=persisted,
        )

    # -- steps 4-7: transitions inside one transaction ---------------------

    def compare(
        self,
        result: DiscoveryResult,
        context: RunContext,
        health: HealthReport,
        persist: PersistOutcome,
    ) -> ComparisonResult:
        """Apply state transitions, or refuse to and say why.

        The refusal path is the important one: an unhealthy run returns
        ``applied=False`` with every counter untouched.
        """
        if not health.valid:
            return ComparisonResult(
                run_id=context.run_id,
                previous_run_id=context.previous_valid_run_id,
                applied=False,
                reason=(
                    f"run health is {health.status}; no status transitions applied "
                    f"({health.reason()}); {len(persist.new_ids)} advertisement row(s) "
                    "were created from the observed inventory but none was counted as "
                    "a discovery -- the next valid run will do that"
                ),
                seen_ids=persist.seen_ids,
                # No transition was applied, so nothing is `new`. The rows that were
                # created are reported separately rather than counted as discoveries.
                new_ids=[],
                observed_new_ids=persist.new_ids,
            )

        # Idempotency guard. The observation and event writes are individually
        # idempotent, but the advertisement counters are computed from current
        # state, so re-applying a run would increment `consecutive_misses` twice
        # and could push a listing to `likely_removed` a day early. The run's
        # `comparison_applied` flag is the idempotency key: once set, the stored
        # outcome is returned instead of being recomputed.
        stored = self.repo.get_run(context.run_id)
        if stored and bool(stored.get("comparison_applied")):
            return self._replayed(context, stored, persist)

        if context.previous_valid_run_id is None and not self.repo.comparison_population(
            context.configuration_hash
        ):
            # First ever run: everything is new, nothing can be missing.
            return self._first_run(context, persist)

        seen = set(persist.seen_ids)
        population = self.repo.comparison_population(context.configuration_hash)
        missing_rows = [r for r in population if str(r["platform_ad_id"]) not in seen]

        outcome = ComparisonResult(
            run_id=context.run_id,
            previous_run_id=context.previous_valid_run_id,
            applied=True,
            reason="valid run; transitions applied",
            seen_ids=persist.seen_ids,
            observed_new_ids=persist.new_ids,
        )

        with self.db.transaction():
            # 4. seen advertisements
            for platform_ad_id in persist.seen_ids:
                self._apply_seen(platform_ad_id, persist.ad_ids[platform_ad_id], context, outcome)
            # 5. missing advertisements
            for row in missing_rows:
                self._apply_missing(row, context, outcome)
            # 6. run counters
            counters = outcome.counters()
            self.repo.update_run(
                context.run_id,
                comparison_applied=True,
                **counters,
            )
        return outcome

    def compare_study(
        self, context: RunContext, health: HealthReport, observations: list[StudyObservation]
    ) -> ComparisonResult:
        """Persist raw evidence and apply only qualified or verified observations.

        The runner commits these rows alongside their detail evidence and the
        final run status in a single transaction.
        """
        outcome = ComparisonResult(
            run_id=context.run_id,
            previous_run_id=context.previous_valid_run_id,
            applied=health.valid,
            reason=health.reason(),
        )
        for observation in observations:
            key = observation.platform_ad_id
            row = self.repo.get_advertisement(key)
            qualified = health.valid and observation.status == "qualified"
            if row is None:
                ad_id = self.repo.upsert_advertisement(
                    platform_ad_id=key,
                    canonical_url=observation.url,
                    current_status=AdStatus.UNKNOWN,
                )
                outcome.observed_new_ids.append(key)
            else:
                ad_id = int(row["id"])
            self.repo.record_observation(
                run_id=context.run_id,
                advertisement_id=ad_id,
                was_seen=qualified,
                observed_at=context.started_at,
                card=observation.card,
                study_evidence={
                    "candidate_present": observation.card is not None,
                    "eligibility_status": observation.status,
                    "eligibility_reason": observation.reason,
                    "eligibility_source": observation.source,
                    "assessed_price_toman": observation.price_toman,
                    "eligible_price_toman": observation.price_toman if qualified else None,
                    "card_price_normalized": observation.price_toman if qualified else None,
                    "raw_card_price_normalized": (
                        observation.card.card_price_normalized if observation.card else None
                    ),
                },
            )

            if qualified:
                outcome.seen_ids.append(key)
                self._apply_study_seen(observation, ad_id, context, outcome)
            elif health.valid and row and row["total_seen_runs"]:
                if row["search_configuration_hash"] != context.configuration_hash:
                    continue
                if observation.status == "missing":
                    self._apply_missing(row, context, outcome)
                elif observation.status in {"price_exit", "out_of_scope"}:
                    self._apply_study_exit(observation, row, context)
                elif row["current_status"] != AdStatus.ACTIVE_OUTSIDE_FILTER:
                    self._apply_study_unresolved(observation, row, context)
        self.repo.update_run(context.run_id, **outcome.counters())
        return outcome

    def _apply_study_seen(
        self,
        observation: StudyObservation,
        ad_id: int,
        context: RunContext,
        outcome: ComparisonResult,
    ) -> None:
        # The raw card remains intact. Price history uses only verified asks
        # from valid observations, including asks resolved from today's detail.
        previous = self.repo.previous_eligible_observation(ad_id, context.run_id)
        if previous and previous["eligible_price_toman"] != observation.price_toman:
            old_price = int(previous["eligible_price_toman"])
            new_price = observation.price_toman
            assert new_price is not None
            change_type = "price_increase" if new_price > old_price else "price_decrease"
            self.repo.record_price_change(
                advertisement_id=ad_id,
                run_id=context.run_id,
                change_type=change_type,
                old_price=old_price,
                new_price=new_price,
                old_price_raw=previous["card_price_raw"],
                new_price_raw=observation.card.card_price_raw if observation.card else None,
                changed_at=context.started_at,
            )
            self.repo.record_event(
                advertisement_id=ad_id,
                run_id=context.run_id,
                event_type=EventType.PRICE_CHANGED,
                previous_status=None,
                new_status=None,
                event_at=context.started_at,
                evidence={
                    "change_type": change_type,
                    "old_price": old_price,
                    "new_price": new_price,
                    "source": observation.source,
                },
            )
        self._apply_seen(observation.platform_ad_id, ad_id, context, outcome)
        self.db.execute(
            "UPDATE advertisements SET search_configuration_hash=?,"
            " first_run_id=COALESCE(first_run_id,?), first_missing_at=NULL,"
            " confirmed_removed_at=NULL, suspected_sold_at=NULL,"
            " filter_exit_reason=NULL, filter_exit_at=NULL, filter_exit_evidence_json=NULL,"
            " sale_label='unknown', sale_confidence=0, detail_availability=? WHERE id=?",
            [context.configuration_hash, context.run_id, str(DetailAvailability.AVAILABLE), ad_id],
        )

    def _apply_study_exit(
        self, observation: StudyObservation, row: dict[str, Any], context: RunContext
    ) -> None:
        if row["current_status"] == AdStatus.ACTIVE_OUTSIDE_FILTER:
            return
        reason = (
            str(FilterExitReason.PRICE_BELOW_FILTER)
            if observation.status == "price_exit"
            else "out_of_scope"
        )
        evidence = {
            "reason": reason,
            "eligibility_reason": observation.reason,
            "assessed_price_toman": observation.price_toman,
            "source": observation.source,
            "status_changed": True,
        }
        self._set_study_nonmember(
            row, context, AdStatus.ACTIVE_OUTSIDE_FILTER, DetailAvailability.OUTSIDE_FILTER
        )
        self.db.execute(
            "UPDATE advertisements SET filter_exit_reason=?, filter_exit_at=?,"
            " filter_exit_evidence_json=? WHERE id=?",
            [reason, context.started_at, self.db.json_dump(evidence), row["id"]],
        )
        self.repo.record_event(
            advertisement_id=int(row["id"]),
            run_id=context.run_id,
            event_type=EventType.FILTER_EXIT,
            previous_status=AdStatus(str(row["current_status"])),
            new_status=AdStatus.ACTIVE_OUTSIDE_FILTER,
            event_at=context.started_at,
            evidence=evidence,
        )

    def _apply_study_unresolved(
        self, observation: StudyObservation, row: dict[str, Any], context: RunContext
    ) -> None:
        # An unresolved day breaks a sequence of verified misses, without
        # evicting the listing from tomorrow's comparison population.
        availability = (
            DetailAvailability.AVAILABLE
            if observation.source == "live_detail"
            else DetailAvailability.UNKNOWN
        )
        self._set_study_nonmember(row, context, AdStatus.UNKNOWN, availability)

    def _set_study_nonmember(
        self,
        row: dict[str, Any],
        context: RunContext,
        status: AdStatus,
        availability: DetailAvailability,
    ) -> None:
        self.db.execute(
            "UPDATE advertisements SET current_status=?, detail_availability=?,"
            " consecutive_misses=0, first_missing_at=NULL, confirmed_removed_at=NULL,"
            " suspected_sold_at=NULL, sale_label='unknown', sale_confidence=0, latest_snapshot_id=NULL,"
            " last_checked_run_id=?, updated_at=? WHERE id=?",
            [str(status), str(availability), context.run_id, utcnow(), row["id"]],
        )

    def _replayed(
        self, context: RunContext, stored: dict[str, Any], persist: PersistOutcome
    ) -> ComparisonResult:
        """Rebuild the outcome of a run whose transitions were already applied.

        Reconstructed from the immutable event log rather than recomputed, so a
        replay reports exactly what the original run did.
        """

        def ids_for(event_type: EventType) -> list[str]:
            return [
                str(row["platform_ad_id"])
                for row in self.repo.events_for_run(context.run_id, event_type)
            ]

        return ComparisonResult(
            run_id=context.run_id,
            previous_run_id=context.previous_valid_run_id,
            applied=True,
            reason=(
                "transitions for this scheduled slot were already applied; "
                "returning the stored outcome without re-incrementing counters"
            ),
            seen_ids=persist.seen_ids,
            new_ids=ids_for(EventType.DISCOVERED),
            observed_new_ids=persist.new_ids,
            missing_ids=ids_for(EventType.MISSING_FIRST_TIME)
            + ids_for(EventType.REMOVAL_CONFIRMED),
            newly_missing_ids=ids_for(EventType.MISSING_FIRST_TIME),
            likely_removed_ids=ids_for(EventType.REMOVAL_CONFIRMED),
            reappeared_ids=ids_for(EventType.REAPPEARED),
            reposted_ids=ids_for(EventType.POSSIBLE_REPOST),
        )

    def _first_run(self, context: RunContext, persist: PersistOutcome) -> ComparisonResult:
        outcome = ComparisonResult(
            run_id=context.run_id,
            previous_run_id=None,
            applied=True,
            reason="first valid run for this configuration; baseline established",
            seen_ids=persist.seen_ids,
            observed_new_ids=persist.new_ids,
        )
        with self.db.transaction():
            for platform_ad_id in persist.seen_ids:
                self._apply_seen(platform_ad_id, persist.ad_ids[platform_ad_id], context, outcome)
            self.repo.update_run(context.run_id, comparison_applied=True, **outcome.counters())
        return outcome

    def _apply_seen(
        self,
        platform_ad_id: str,
        advertisement_id: int,
        context: RunContext,
        outcome: ComparisonResult,
    ) -> None:
        row = self.repo.get_advertisement(platform_ad_id)
        assert row is not None  # persisted in step 2
        previous_status = AdStatus(str(row["current_status"]))
        is_first = int(row["total_seen_runs"] or 0) == 0

        state = _state_from_row(row)
        transition = on_seen(
            state if not is_first else None,
            observed_at=context.started_at,
            is_first_discovery=is_first,
        )
        new_state = transition.state

        self.repo.upsert_advertisement(
            platform_ad_id=platform_ad_id,
            canonical_url=str(row["canonical_url"]),
            current_status=new_state.status,
            consecutive_misses=0,
            total_seen_runs=new_state.total_seen_runs,
            first_seen_at=new_state.first_seen_at,
            last_seen_at=new_state.last_seen_at,
            reappeared_at=new_state.reappeared_at,
            last_seen_run_id=context.run_id,
            last_checked_run_id=context.run_id,
        )
        for event_type, evidence in transition.events:
            self.repo.record_event(
                advertisement_id=advertisement_id,
                run_id=context.run_id,
                event_type=event_type,
                previous_status=previous_status,
                new_status=new_state.status,
                event_at=context.started_at,
                evidence=evidence,
            )
            if event_type is EventType.REAPPEARED:
                outcome.reappeared_ids.append(platform_ad_id)
            elif event_type is EventType.DISCOVERED:
                outcome.new_ids.append(platform_ad_id)

        outcome.transitions.append(
            StatusTransition(
                platform_ad_id=platform_ad_id,
                advertisement_id=advertisement_id,
                previous_status=previous_status,
                new_status=new_state.status,
                event_type=EventType.SEEN,
            )
        )

    def _apply_missing(
        self, row: dict[str, Any], context: RunContext, outcome: ComparisonResult
    ) -> None:
        platform_ad_id = str(row["platform_ad_id"])
        advertisement_id = int(row["id"])
        previous_status = AdStatus(str(row["current_status"]))
        state = _state_from_row(row)

        transition = on_missing(
            state,
            run_started_at=context.started_at,
            removal_confirmation_misses=self.cfg.removal_confirmation_misses,
        )
        new_state = transition.state

        self.repo.upsert_advertisement(
            platform_ad_id=platform_ad_id,
            canonical_url=str(row["canonical_url"]),
            current_status=new_state.status,
            consecutive_misses=new_state.consecutive_misses,
            total_missing_runs=new_state.total_missing_runs,
            first_missing_at=new_state.first_missing_at,
            confirmed_removed_at=new_state.confirmed_removed_at,
            last_checked_run_id=context.run_id,
        )
        # Absence is an observation, recorded as such.
        self.repo.record_observation(
            run_id=context.run_id,
            advertisement_id=advertisement_id,
            was_seen=False,
            observed_at=context.started_at,
        )
        for event_type, evidence in transition.events:
            self.repo.record_event(
                advertisement_id=advertisement_id,
                run_id=context.run_id,
                event_type=event_type,
                previous_status=previous_status,
                new_status=new_state.status,
                event_at=context.started_at,
                evidence=evidence,
            )

        outcome.missing_ids.append(platform_ad_id)
        if new_state.consecutive_misses == 1:
            outcome.newly_missing_ids.append(platform_ad_id)
        if (
            new_state.status is AdStatus.LIKELY_REMOVED
            and previous_status is not AdStatus.LIKELY_REMOVED
        ):
            outcome.likely_removed_ids.append(platform_ad_id)

        outcome.transitions.append(
            StatusTransition(
                platform_ad_id=platform_ad_id,
                advertisement_id=advertisement_id,
                previous_status=previous_status,
                new_status=new_state.status,
                event_type=(
                    EventType.REMOVAL_CONFIRMED
                    if new_state.status is AdStatus.LIKELY_REMOVED
                    else EventType.MISSING_FIRST_TIME
                ),
            )
        )


def _state_from_row(row: dict[str, Any]) -> AdState:
    return AdState(
        status=AdStatus(str(row["current_status"])),
        consecutive_misses=int(row.get("consecutive_misses") or 0),
        total_seen_runs=int(row.get("total_seen_runs") or 0),
        total_missing_runs=int(row.get("total_missing_runs") or 0),
        first_seen_at=parse_ts(row.get("first_seen_at")),
        last_seen_at=parse_ts(row.get("last_seen_at")),
        first_missing_at=parse_ts(row.get("first_missing_at")),
        confirmed_removed_at=parse_ts(row.get("confirmed_removed_at")),
        reappeared_at=parse_ts(row.get("reappeared_at")),
    )


def finalize_run(
    db: Database,
    context: RunContext,
    health: HealthReport,
    comparison: ComparisonResult,
    *,
    detail_success: int = 0,
    detail_failure: int = 0,
) -> None:
    """Write the run's terminal status and counters."""
    repo = Repository(db)
    error_count = int(
        db.scalar("SELECT COUNT(*) FROM scrape_errors WHERE run_id=?", [context.run_id]) or 0
    )
    repo.update_run(
        context.run_id,
        finished_at=utcnow(),
        status=str(health.status),
        health_reason=health.reason(),
        detail_success_count=detail_success,
        detail_failure_count=detail_failure,
        error_count=error_count,
        comparison_applied=comparison.applied,
    )


def skipped_run(
    db: Database,
    *,
    search_url: str,
    scheduled_for: datetime | None,
    cfg: MonitorConfig,
    holder: str | None,
    trigger_type: str = "manual",
    is_synthetic: bool = False,
    production_schedule_name: str | None = None,
) -> int:
    """Record a run that never started because another one held the lock.

    The real trigger is carried through rather than defaulted to ``manual``: a
    scheduled trigger that fired and was declined is not a human running the
    pipeline by hand, and recording it as one would falsify the audit trail.

    Such a row does not own the slot — ``uq_production_slot`` excludes skipped
    runs — so the slot stays unfulfilled and reconciliation can still catch it up.
    """
    repo = Repository(db)
    run_id, _ = repo.create_or_get_run(
        search_url=search_url,
        scheduled_for=scheduled_for,
        timezone=cfg.timezone,
        configuration_hash=cfg.configuration_hash(),
        scraper_version="n/a",
        monitor_version="n/a",
        previous_valid_run_id=None,
        trigger_type=trigger_type,
        is_synthetic=is_synthetic,
        production_schedule_name=production_schedule_name,
        study_profile=getattr(cfg, "study_profile", "legacy"),
    )
    repo.update_run(
        run_id,
        status=str(RunHealth.SKIPPED_DUE_TO_EXISTING_RUN),
        finished_at=utcnow(),
        health_reason=f"another run holds the execution lock (holder={holder})",
    )
    repo.record_alert(
        run_id=run_id,
        severity="warning",
        alert_type="overlapping_run_skipped",
        message="scheduled run skipped because a previous run is still active",
        metadata={"holder": holder},
    )
    return run_id
