"""Evidence-graded eligibility for the nationwide passenger-car series.

The opt-in ``nationwide_passenger_v1`` study follows every passenger car on the
site (any origin, either seller type) with a production year of 1397 Jalali /
2018 Gregorian or later and a *verified* full asking price strictly above
1,000,000,000 Toman. This module answers, for one card or one detail record,
which side of that line the listing is on, and refuses to answer when the
evidence does not support an answer.

One rule, two entry points
--------------------------
:func:`assess_card` and :func:`assess_detail` only *gather* claims from their
input into an :class:`Observation`. Both hand it to :func:`decide`, so a card and
a detail record carrying the same evidence get the same decision.

Statuses
--------
``qualified``
    Verified full asking price > 1,000,000,000 Toman, body confirmed as
    ``passenger_car`` and production year confirmed in scope.
``price_exit``
    Verified full asking price <= 1,000,000,000 Toman (equality included): the
    listing is alive but has left the price band.
``out_of_scope``
    The listing's own evidence places it outside the series: a confirmed
    non-passenger body, or a confirmed production year below the floor.
``unknown``
    Anything else. The default whenever evidence is missing, stale, malformed or
    self-contradictory.

What counts as a verified full asking price
-------------------------------------------
1. Every source that types the offer types it ``lumpsum``, and at least one
   does. Installment, negotiable and hidden offers are not a full ask: positive
   installment totals and deposit-only amounts were both observed live.
2. No source marks it hidden, and no source marks it pre-sale (pre-sale listings
   typed ``lumpsum`` were observed live; their amount is not supported). An
   absent ``is_pre_sale`` is normal on ordinary cash listings and is accepted.
3. No source shows instalment terms (a positive deposit or instalment).
4. The **unit is proven by a displayed amount**, never assumed: a strictly
   formatted number whose own text carries the formal word ``تومان``. Rial
   displays are not converted.
5. That displayed amount is **corroborated by the same listing's current typed
   cash amount** (the detail API's ``price.price`` under ``price.type ==
   lumpsum``; for a card, the card's own typed raw amount).
6. Every amount for the offer, displayed or bare, is strictly formatted, positive
   and identical.

Source evidence
---------------
* Search API card and detail API: bare grouped ASCII amount (``5,300,000,000``)
  plus ``type``; no unit. ``is_pre_sale`` is absent on ordinary listings.
* Detail HTML header: the displayed current price is a number with an adjacent,
  separate ``<span>تومان</span>`` (2026-11-20 commissioning probes, two cash
  listings; see :func:`bama_deep.ad_html.extract_display_price`). This is the
  only observed unit proof. It reaches :func:`assess_detail` as the
  ``price_display_*`` keys added by
  :class:`bama_monitor.scrapers.BamaDetailScraper`.
* JSON-LD ``offers.priceCurrency`` declared ``IRR`` for the very figure the
  header shows in Toman, so JSON-LD pricing is ignored entirely.
* The base model's ``price_currency = "IRT"`` is a hard-coded default, not an
  observation, and is ignored.

Consequently a production search card is never more than ``unknown`` or
``out_of_scope``: it carries no unit proof. Price-band decisions are made from
detail records.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from bama_scraper.normalization import (
    ARABIC_DIGITS,
    ASCII_DIGITS,
    PERSIAN_DIGITS,
    convert_digits,
    normalize_text,
    parse_strict_integer,
    parse_unit_marked_amount,
)

#: Configuration value that opts a monitor into this study.
NATIONWIDE_PROFILE = "nationwide_passenger_v1"
#: Default profile: none of this module's rules apply.
LEGACY_PROFILE = "legacy"

#: Inclusive upper bound for ``price_exit``; ``qualified`` needs strictly more.
PRICE_THRESHOLD_TOMAN = 1_000_000_000
#: Production-year floors, compared in the calendar the listing itself used.
MIN_JALALI_YEAR = 1397
MIN_GREGORIAN_YEAR = 2018
#: The only body slug in scope.
PASSENGER_BODY = "passenger_car"

#: Plausible production-year ranges per calendar; anything else is unreadable.
_JALALI_RANGE = (1300, 1450)
_GREGORIAN_RANGE = (1900, 2100)

#: A body value recognisable as a site slug (rather than free text).
_BODY_SLUG_RE = re.compile(r"[a-z][a-z0-9_]*")

#: Offer types and why a non-lump-sum type is not a full asking price.
_NON_FULL_ASK_TYPES = {
    "installment": "installment_offer_not_full_ask",
    "negotiable": "negotiable_without_asking_price",
    "hidden": "price_hidden",
    "notshown": "price_hidden",
}

#: API price keys that, when positive, mean the offer carries instalment terms.
_API_TERM_KEYS = (
    "price.prepayment",
    "price.payment",
    "price.prepayment_primary",
    "price.prepayment_secondary",
    "price.payment_primary",
)

#: Bare amounts that can corroborate a display, mapped to the type claim that
#: must accompany them (the amount is only "cash" if its own source typed it).
_CORROBORATING = {"api_price": "api", "card_price": "card"}


class EligibilityStatus(StrEnum):
    QUALIFIED = "qualified"
    PRICE_EXIT = "price_exit"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """Outcome of :func:`decide`.

    ``price_toman`` is set only when the asking price was verified; it is the
    single agreed amount, in Toman.
    """

    status: EligibilityStatus
    reason: str
    price_toman: int | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_qualified(self) -> bool:
        return self.status is EligibilityStatus.QUALIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "reason": self.reason,
            "price_toman": self.price_toman,
            "evidence": dict(self.evidence),
        }


@dataclass(slots=True)
class Observation:
    """Every claim one sighting makes, tagged with where it came from.

    Lists hold ``(source, value)`` pairs. An absent claim is not recorded; a
    present but unreadable one is recorded as-is so the rule can reject it.
    """

    price_types: list[tuple[str, Any]] = field(default_factory=list)
    #: Amounts whose own text states the currency (``"5,300,000,000 تومان"``).
    displays: list[tuple[str, Any]] = field(default_factory=list)
    #: Same-offer amounts with no unit of their own.
    bare_amounts: list[tuple[str, Any]] = field(default_factory=list)
    #: ``True`` = pre-sale; ``False`` = not; anything else = unsupported.
    presale: list[tuple[str, Any]] = field(default_factory=list)
    hidden: list[tuple[str, Any]] = field(default_factory=list)
    #: Sources showing instalment terms (deposit/instalment) on this offer.
    installment_terms: list[str] = field(default_factory=list)
    years: list[tuple[str, Any]] = field(default_factory=list)
    bodies: list[tuple[str, Any]] = field(default_factory=list)
    stale: bool = False
    problems: list[str] = field(default_factory=list)

    def add_amount_text(self, source: str, raw: Any) -> None:
        """Route an amount to ``displays`` if its text names a currency."""
        if raw is None or raw == "":
            return
        if isinstance(raw, str) and parse_unit_marked_amount(raw) is not None:
            self.displays.append((source, raw))
        else:
            self.bare_amounts.append((source, raw))


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def decide(obs: Observation) -> EligibilityDecision:
    """Apply the single eligibility rule to an :class:`Observation`."""
    if obs.stale:
        return EligibilityDecision(
            EligibilityStatus.UNKNOWN,
            "evidence_not_from_current_observation",
            evidence={"stale": True},
        )

    body_state, body_detail = _assess_body(obs.bodies)
    year_state, year_detail = _assess_year(obs.years)
    evidence: dict[str, Any] = {"body": body_detail, "year": year_detail}

    if body_state == "outside":
        return EligibilityDecision(
            EligibilityStatus.OUT_OF_SCOPE, f"body_not_passenger:{body_detail}", evidence=evidence
        )
    if year_state == "below_floor":
        return EligibilityDecision(
            EligibilityStatus.OUT_OF_SCOPE, f"year_below_floor:{year_detail}", evidence=evidence
        )

    amount, price_reason, price_evidence = _verify_price(obs)
    evidence["price"] = price_evidence
    if amount is None:
        return EligibilityDecision(EligibilityStatus.UNKNOWN, price_reason, evidence=evidence)
    if amount <= PRICE_THRESHOLD_TOMAN:
        return EligibilityDecision(
            EligibilityStatus.PRICE_EXIT,
            "verified_full_ask_at_or_below_threshold",
            price_toman=amount,
            evidence=evidence,
        )
    if body_state != "passenger":
        return EligibilityDecision(
            EligibilityStatus.UNKNOWN, f"body_unconfirmed:{body_detail}", amount, evidence
        )
    if year_state != "in_scope":
        return EligibilityDecision(
            EligibilityStatus.UNKNOWN, f"year_unconfirmed:{year_detail}", amount, evidence
        )
    return EligibilityDecision(
        EligibilityStatus.QUALIFIED,
        "verified_full_ask_above_threshold",
        price_toman=amount,
        evidence=evidence,
    )


def _assess_body(claims: list[tuple[str, Any]]) -> tuple[str, str]:
    """``passenger`` | ``outside`` | ``unknown`` with a detail string."""
    if not claims:
        return "unknown", "missing"
    slugs: set[str] = set()
    for source, raw in claims:
        if not isinstance(raw, str) or not _BODY_SLUG_RE.fullmatch(raw.strip()):
            return "unknown", f"unreadable@{source}"
        slugs.add(raw.strip())
    if len(slugs) > 1:
        return "unknown", "conflict:" + ",".join(sorted(slugs))
    slug = next(iter(slugs))
    return ("passenger", slug) if slug == PASSENGER_BODY else ("outside", slug)


def native_year(raw: Any) -> tuple[str, int] | None:
    """``(calendar, year)`` read from the raw value in its own calendar.

    No conversion is applied: a Jalali year is compared with the Jalali floor
    and a Gregorian year with the Gregorian floor, which avoids the off-by-one a
    ``+621`` conversion introduces across the new-year boundary.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip()
        scripts = {
            "ascii" if ch in ASCII_DIGITS else "persian" if ch in PERSIAN_DIGITS else "arabic"
            for ch in text
            if ch in ASCII_DIGITS or ch in PERSIAN_DIGITS or ch in ARABIC_DIGITS
        }
        text = convert_digits(text)
        if len(scripts) != 1 or not re.fullmatch(r"\d{4}", text):
            return None
        value = int(text)
    else:
        return None
    if _JALALI_RANGE[0] <= value <= _JALALI_RANGE[1]:
        return "jalali", value
    if _GREGORIAN_RANGE[0] <= value <= _GREGORIAN_RANGE[1]:
        return "gregorian", value
    return None


def _assess_year(claims: list[tuple[str, Any]]) -> tuple[str, str]:
    """``in_scope`` | ``below_floor`` | ``unknown`` with a detail string."""
    if not claims:
        return "unknown", "missing"
    parsed: set[tuple[str, int]] = set()
    for source, raw in claims:
        year = native_year(raw)
        if year is None:
            return "unknown", f"unreadable@{source}"
        parsed.add(year)
    if len(parsed) > 1:
        return "unknown", "conflict:" + ",".join(f"{c}:{y}" for c, y in sorted(parsed))
    calendar, value = next(iter(parsed))
    floor = MIN_JALALI_YEAR if calendar == "jalali" else MIN_GREGORIAN_YEAR
    detail = f"{calendar}:{value}"
    return ("in_scope", detail) if value >= floor else ("below_floor", detail)


def _verify_price(obs: Observation) -> tuple[int | None, str, dict[str, Any]]:
    """Return ``(toman, reason, evidence)``; ``toman`` is None unless verified."""
    evidence: dict[str, Any] = {}
    if obs.problems:
        return None, obs.problems[0], evidence

    # 1. Offer type: every typing source must say lump sum.
    types: dict[str, Any] = {}
    for source, value in obs.price_types:
        types.setdefault(source, value.strip().lower() if isinstance(value, str) else value)
    evidence["types"] = {s: str(v) for s, v in sorted(types.items())}
    if not types:
        return None, "price_type_unknown", evidence
    values = set(types.values())
    if values != {"lumpsum"}:
        if any(not isinstance(v, str) for v in values):
            return None, "price_type_unreadable", evidence
        if len(values) > 1:
            return None, "price_type_conflict", evidence
        value = next(iter(values))
        return None, _NON_FULL_ASK_TYPES.get(value, f"unsupported_price_type:{value}"), evidence

    # 2. Hidden, pre-sale, instalment terms.
    if any(flag is not False for _, flag in obs.hidden):
        return None, "price_hidden", evidence
    if any(flag is not False for _, flag in obs.presale):
        return None, "presale_unsupported", evidence
    if obs.installment_terms:
        evidence["installment_terms"] = list(obs.installment_terms)
        return None, "installment_terms_on_offer", evidence

    # 3. Amounts, all strictly formatted.
    bare: dict[str, int] = {}
    for source, raw in obs.bare_amounts:
        value = parse_strict_integer(raw)
        if value is None:
            return None, f"malformed_amount@{source}", evidence
        bare[source] = value
    toman: dict[str, int] = {}
    for source, raw in obs.displays:
        parsed = parse_unit_marked_amount(raw)
        if parsed is None:
            return None, f"malformed_display@{source}", evidence
        if parsed[1] != "toman":
            return None, f"rial_amount_unsupported@{source}", evidence
        toman[source] = parsed[0]
    evidence["bare"] = dict(bare)
    evidence["displayed_toman"] = dict(toman)
    if any(v == 0 for v in (*bare.values(), *toman.values())):
        return None, "zero_price", evidence

    # 4. Unit proof, corroborated by the listing's current typed cash amount.
    if not toman:
        return None, "price_unit_unproven", evidence
    corroborating = [
        src for src, type_src in _CORROBORATING.items() if src in bare and type_src in types
    ]
    if not corroborating:
        return None, "display_without_typed_cash_amount", evidence
    every = {*toman.values(), *bare.values()}
    if len(every) != 1:
        return None, "amount_conflict", evidence
    evidence["corroborated_by"] = corroborating
    return next(iter(every)), "verified", evidence


# ---------------------------------------------------------------------------
# Entry point: search card
# ---------------------------------------------------------------------------


def observe_card(card: Any) -> Observation:
    """Gather the claims a :class:`~bama_monitor.models.DiscoveredCard` makes.

    Only the card's own evidence fields are read. ``card_price_normalized`` is
    the legacy permissive parse and proves nothing, so it is ignored.
    """
    obs = Observation(stale=getattr(card, "card_evidence_current", None) is False)
    if getattr(card, "card_price_type", None) is not None:
        obs.price_types.append(("card", card.card_price_type))
    raw = getattr(card, "card_price_raw", None)
    obs.add_amount_text("card_price", raw)
    # A unit label is accepted only when the card's own text proves it.
    label = getattr(card, "card_price_unit", None)
    if label is not None:
        marked = parse_unit_marked_amount(raw)
        if marked is None or marked[1] != label:
            obs.problems.append("unit_label_without_marked_amount@card")
    if getattr(card, "card_is_pre_sale", None) is not None:
        obs.presale.append(("card", card.card_is_pre_sale))
    if getattr(card, "card_year", None) is not None:
        obs.years.append(("card", card.card_year))
    if getattr(card, "card_body_type", None) is not None:
        obs.bodies.append(("card", card.card_body_type))
    return obs


def assess_card(card: Any) -> EligibilityDecision:
    """Eligibility of one search card under the nationwide rule."""
    return decide(observe_card(card))


# ---------------------------------------------------------------------------
# Entry point: detail record
# ---------------------------------------------------------------------------

_UNREADABLE = object()


def _load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return _UNREADABLE
    return None


def _positive_or_unreadable(raw: Any) -> bool:
    value = parse_strict_integer(raw)
    return value is None or value > 0


def _observe_api(raw: Mapping[str, Any], obs: Observation) -> None:
    """Claims from the flattened detail-API payload (``api_raw_json``)."""
    if "price.type" in raw:
        obs.price_types.append(("api", raw["price.type"]))
    if "price.price" in raw:
        obs.add_amount_text("api_price", raw["price.price"])
    if "detail.is_pre_sale" in raw:
        obs.presale.append(("api", raw["detail.is_pre_sale"]))
    if raw.get("detail.pre_sale_delivery") not in (None, ""):
        obs.presale.append(("api_delivery", True))
    for key in _API_TERM_KEYS:
        if key in raw and _positive_or_unreadable(raw[key]):
            obs.installment_terms.append(f"api:{key}")
    if "detail.year" in raw:
        obs.years.append(("api", raw["detail.year"]))
    if "detail.body_type" in raw:
        obs.bodies.append(("api", raw["detail.body_type"]))


def _observe_html(payload: Mapping[str, Any], obs: Observation) -> None:
    """Claims from the resolved Nuxt payload (``html_payload_json``)."""
    price = payload.get("price")
    if isinstance(price, dict):
        if price.get("type") is not None:
            obs.price_types.append(("html", price["type"]))
        fixed = price.get("fixed")
        if isinstance(fixed, dict):
            if fixed.get("value") is not None:
                obs.bare_amounts.append(("html_fixed_value", fixed["value"]))
            # The rendered label's format is not observed for cash listings. It
            # counts only when it is strictly an amount (with or without the
            # currency word); a worded rendering is not an amount claim at all,
            # and the typed ``fixed.value`` above must still agree.
            text = fixed.get("text")
            if parse_unit_marked_amount(text) is not None or parse_strict_integer(text):
                obs.add_amount_text("html_fixed_text", text)
        if price.get("installment") not in (None, {}, []):
            obs.installment_terms.append("html:installment")
        if price.get("presale") not in (None, {}, []):
            obs.presale.append(("html", True))
    vehicle = payload.get("vehicle")
    if isinstance(vehicle, dict):
        year = vehicle.get("year")
        if isinstance(year, dict):
            raw_year = year.get("value") if year.get("value") is not None else year.get("text")
            if raw_year is not None:
                obs.years.append(("html", raw_year))
        body_type = vehicle.get("bodyType")
        if isinstance(body_type, dict) and body_type.get("value") is not None:
            obs.bodies.append(("html", body_type["value"]))
    body = payload.get("body")
    if isinstance(body, dict) and body.get("value") is not None:
        obs.bodies.append(("html_body", body["value"]))


def observe_detail(fields: Mapping[str, Any]) -> Observation:
    """Gather the claims a detail record (``DetailResult.fields``) makes.

    Each source is read from its own retained evidence (``api_raw_json``,
    ``html_payload_json``) so the merge's precedence cannot hide a disagreement;
    the merged flat fields derive from the same sources and can only agree or
    expose a conflict. The header display arrives as ``price_display_amount_text``
    + ``price_display_unit_text``. Never read: ``json_ld_json`` pricing
    (mislabelled ``IRR``) and ``price_currency`` (hard-coded default).
    ``observed_fresh=False`` marks the record as not from this observation.
    """
    obs = Observation(stale=fields.get("observed_fresh") is False)

    for key, observer in (("api_raw_json", _observe_api), ("html_payload_json", _observe_html)):
        blob = _load_json(fields.get(key))
        if blob is _UNREADABLE or (blob is not None and not isinstance(blob, dict)):
            obs.problems.append(f"unreadable_evidence@{key}")
        elif isinstance(blob, dict):
            observer(blob, obs)

    amount_text = fields.get("price_display_amount_text")
    unit_text = fields.get("price_display_unit_text")
    if amount_text is not None or unit_text is not None:
        if not isinstance(amount_text, str) or not isinstance(unit_text, str):
            obs.problems.append("incomplete_display@html_header")
        else:
            obs.displays.append(("html_header", f"{amount_text} {normalize_text(unit_text)}"))

    # Merged flat fields.
    if fields.get("price_type") is not None:
        obs.price_types.append(("record", fields["price_type"]))
    obs.add_amount_text("record_price_text", fields.get("price_text"))
    if fields.get("price_toman") is not None:
        obs.bare_amounts.append(("record_price_toman", fields["price_toman"]))
    if fields.get("is_pre_sale") is not None:
        obs.presale.append(("record", fields["is_pre_sale"]))
    if fields.get("price_hidden") is not None:
        obs.hidden.append(("record", fields["price_hidden"]))
    if fields.get("is_installment") is True:
        obs.installment_terms.append("record:is_installment")
    for key in ("down_payment_toman", "installment_amount_toman", "prepayment_secondary_toman"):
        if fields.get(key) not in (None, 0):
            obs.installment_terms.append(f"record:{key}")
    if fields.get("year_text") is not None:
        obs.years.append(("record", fields["year_text"]))
    if fields.get("body_type") is not None:
        obs.bodies.append(("record", fields["body_type"]))
    return obs


def assess_detail(fields: Mapping[str, Any]) -> EligibilityDecision:
    """Eligibility of one detail record under the nationwide rule."""
    return decide(observe_detail(fields))


__all__ = [
    "LEGACY_PROFILE",
    "MIN_GREGORIAN_YEAR",
    "MIN_JALALI_YEAR",
    "NATIONWIDE_PROFILE",
    "PASSENGER_BODY",
    "PRICE_THRESHOLD_TOMAN",
    "EligibilityDecision",
    "EligibilityStatus",
    "Observation",
    "assess_card",
    "assess_detail",
    "decide",
    "native_year",
    "observe_card",
    "observe_detail",
]
