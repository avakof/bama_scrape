"""Evidence, eligibility and propagation for the nationwide passenger-car profile.

Tests use the repository's offline API and search fixtures. The synthetic cash
detail page contains a toman header, two unrelated price widgets, and an IRR
JSON-LD label for the same amount.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from bama_monitor.config import DetailPolicy, MonitorConfig
from bama_monitor.eligibility import (
    NATIONWIDE_PROFILE,
    PRICE_THRESHOLD_TOMAN,
    EligibilityStatus,
    assess_card,
    assess_detail,
    native_year,
)
from bama_monitor.models import DiscoveredCard
from bama_monitor.scrapers import BamaDiscoveryScraper, display_price_fields
from bama_scraper.discovery import _dom_card_to_ad, card_to_ad
from bama_scraper.models import DiscoveredAd
from bama_scraper.normalization import parse_strict_integer, parse_unit_marked_amount
from bama_scraper.storage import Storage

REPO = Path(__file__).resolve().parents[1]
DEEP_FIXTURES = REPO / "deep_scraper" / "tests" / "fixtures"
BASE_FIXTURES = REPO / "tests" / "fixtures"
HEADER_FIXTURE = Path(__file__).parent / "fixtures" / "detail_cash_header_price.html"

QUALIFIED = EligibilityStatus.QUALIFIED
PRICE_EXIT = EligibilityStatus.PRICE_EXIT
OUT = EligibilityStatus.OUT_OF_SCOPE
UNKNOWN = EligibilityStatus.UNKNOWN


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _grouped(n: int) -> str:
    return f"{n:,}"


def api_raw(
    *,
    price: Any = "5,300,000,000",
    price_type: Any = "lumpsum",
    year: Any = "1403",
    body: Any = "passenger_car",
    **extra: Any,
) -> dict[str, Any]:
    """A flattened detail-API payload as ``merge_ad`` stores it."""
    raw: dict[str, Any] = {
        "price.type": price_type,
        "price.price": price,
        "price.prepayment_primary": "0",
        "price.prepayment_secondary": "0",
        "price.payment_primary": "0",
        "detail.year": year,
        "detail.body_type": body,
    }
    raw.update(extra)
    return {k: v for k, v in raw.items() if v is not None}


def detail_fields(
    *,
    display: Any = "5,300,000,000",
    unit: Any = "تومان",
    **api: Any,
) -> dict[str, Any]:
    """Minimal ``DetailResult.fields``: API evidence plus the header display."""
    fields: dict[str, Any] = {"api_raw_json": json.dumps(api_raw(**api), ensure_ascii=False)}
    if display is not None:
        fields["price_display_amount_text"] = display
        fields["price_display_unit_text"] = unit
        fields["price_display_source"] = "html_listing_header_price"
    return fields


def at(amount: int, **kw: Any) -> dict[str, Any]:
    return detail_fields(display=_grouped(amount), price=_grouped(amount), **kw)


def api_payload(name: str = "api_detail_personal.json", **edits: Any) -> dict[str, Any]:
    payload = json.loads((DEEP_FIXTURES / name).read_text(encoding="utf-8"))
    data = payload["data"]
    data["detail"]["year"] = edits.pop("year", "1403")
    data["price"]["price"] = edits.pop("price", "5,300,000,000")
    if edits.pop("drop_presale", True):
        data["detail"].pop("is_pre_sale", None)
    for key, value in edits.items():
        section, _, field_name = key.partition("__")
        data[section][field_name] = value
    return payload


def adapter_fields(html: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """What ``BamaDetailScraper.scrape`` puts in ``DetailResult.fields``."""
    from bama_deep.ad_api import parse_ad_api
    from bama_deep.ad_html import parse_ad_html
    from bama_deep.merge import merge_ad

    api = parse_ad_api(payload, now_ts=0) if payload is not None else None
    page = parse_ad_html(html, now_ts=0)
    merged = merge_ad(
        api, page, ad_id="demo0001", url="https://example.invalid/car/detail-demo0001"
    )
    return {**merged.record, **display_price_fields(page)}


@pytest.fixture(scope="module")
def header_html() -> str:
    return HEADER_FIXTURE.read_text(encoding="utf-8")


HEADER_ROW = (
    '<div class="flex items-end gap-2 self-stretch">'
    '<div class="flex flex-col justify-end items-start">'
    '<div class="flex items-center gap-1 text-neutral-10">{inner}</div></div>'
    "{extra}</div>"
)
UNIT_SPAN = (
    '<span class="inline-block text-right w-full text-sm leading-6 font-normal '
    'max-w-max flex-1">تومان</span>'
)


def header(inner: str, extra: str = "<div>دقایقی پیش</div>") -> str:
    return HEADER_ROW.format(inner=inner, extra=extra)


# ---------------------------------------------------------------------------
# strict numbers and units
# ---------------------------------------------------------------------------


class TestStrictAmounts:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("5,300,000,000", 5_300_000_000),
            ("۵٬۳۰۰٬۰۰۰٬۰۰۰", 5_300_000_000),
            ("٥،٣٠٠،٠٠٠،٠٠٠", 5_300_000_000),
            ("1000000000", 1_000_000_000),
            ("0", 0),
            (5_300_000_000, 5_300_000_000),
            (5.3e9, 5_300_000_000),
        ],
    )
    def test_accepts_one_strict_amount(self, raw: Any, expected: int) -> None:
        assert parse_strict_integer(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "-1,000,000,000",
            "+1,000,000,000",
            "1,000,000,000.5",
            "1.5",
            "1,25,000",
            "1,000,0000",
            "1,000٬000",  # two separators
            "1,۰۰۰,000",  # mixed scripts
            "1 000 000",
            "01,000",
            "۱ میلیارد",
            "",
            True,
            -5,
            1.5,
            None,
        ],
    )
    def test_rejects_anything_else(self, raw: Any) -> None:
        assert parse_strict_integer(raw) is None

    def test_unit_comes_from_the_text_only(self) -> None:
        assert parse_unit_marked_amount("5,300,000,000 تومان") == (5_300_000_000, "toman")
        assert parse_unit_marked_amount("۵٬۳۰۰٬۰۰۰٬۰۰۰تومان") == (5_300_000_000, "toman")
        assert parse_unit_marked_amount("53,000,000,000 ریال") == (53_000_000_000, "rial")
        for text in (
            "5,300,000,000",
            "قیمت 5,300,000,000 تومان",
            "5,300 میلیون تومان",
            "5,300,000,000 تومن",
            "5,300,000,000 IRT",
            "-5,300,000,000 تومان",
        ):
            assert parse_unit_marked_amount(text) is None, text


# ---------------------------------------------------------------------------
# header display extraction
# ---------------------------------------------------------------------------


class TestDisplayExtraction:
    def test_fixture_header_is_found_and_decoys_are_not(self, header_html: str) -> None:
        from bama_deep.ad_html import extract_display_price

        found = extract_display_price(header_html)
        # price-history (4,900,000,000) and market-price (5,100,000,000) widgets
        # are on the same page; only the listing header is read.
        assert found == {
            "amount_text": "5,300,000,000",
            "unit_text": "تومان",
            "source": "html_listing_header_price",
            "candidates": 1,
        }

    @pytest.mark.parametrize(
        "inner",
        [
            f"5,300,000,000 {UNIT_SPAN}",  # bare text node
            f"<span>5,300,000,000</span>{UNIT_SPAN}",  # sibling span
            f"<span><b>5,300,000,000</b></span> {UNIT_SPAN}",  # nested
        ],
    )
    def test_number_node_shapes(self, inner: str) -> None:
        from bama_deep.ad_html import extract_display_price

        found = extract_display_price(f"<html><body>{header(inner)}</body></html>")
        assert found is not None and found["amount_text"] == "5,300,000,000"

    def test_class_order_and_additions_are_tolerated(self) -> None:
        from bama_deep.ad_html import extract_display_price

        html = (
            '<div class="px-3 self-stretch items-end flex gap-4 md:gap-2">'
            '<div class="items-start flex-col flex">'
            '<div class="text-neutral-10 items-center flex extra">'
            f"<span>2,390,000,000</span>{UNIT_SPAN}</div></div></div>"
        )
        found = extract_display_price(html)
        assert found is not None and found["amount_text"] == "2,390,000,000"

    @pytest.mark.parametrize(
        ("html", "why"),
        [
            (header("<span>5,300,000,000</span>"), "no unit span"),
            (header(f"<span>پیش پرداخت</span><span>1,000,000,000</span>{UNIT_SPAN}"), "label"),
            (header(f"<span>5,300,000,000</span><span>4,900,000,000</span>{UNIT_SPAN}"), "two"),
            (header("<span>5,300,000,000</span><span>تومان ناقابل</span>"), "unit+word"),
            (
                header(
                    f"<span>5,300,000,000</span>{UNIT_SPAN}",
                    extra="<div><span>پیش پرداخت 1,000,000,000</span><span>تومان</span></div>",
                ),
                "second amount in header",
            ),
            (
                f'<div class="flex items-center"><span>5,300,000,000</span>{UNIT_SPAN}</div>',
                "not in the listing header",
            ),
            (
                '<section class="price-history"><div class="flex flex-col">'
                '<div class="flex items-center gap-1"><span>4,900,000,000</span>'
                f"{UNIT_SPAN}</div></div></section>",
                "same row and column, but a non-header container",
            ),
            (
                header(f"<span>5,300,000,000</span>{UNIT_SPAN}")
                + header(f"<span>4,300,000,000</span>{UNIT_SPAN}"),
                "two headers disagree",
            ),
            (
                '<script type="application/ld+json">{"@type":"Product","offers":'
                '{"@type":"Offer","price":5300000000,"priceCurrency":"IRR"}}</script>',
                "json-ld only",
            ),
            ("", "empty"),
        ],
    )
    def test_ambiguous_or_unrelated_widgets_yield_nothing(self, html: str, why: str) -> None:
        from bama_deep.ad_html import extract_display_price

        assert extract_display_price(f"<html><body>{html}</body></html>") is None, why

    def test_identical_duplicate_headers_are_one_reading(self) -> None:
        from bama_deep.ad_html import extract_display_price

        one = header(f"<span>5,300,000,000</span>{UNIT_SPAN}")
        found = extract_display_price(one + one)
        assert found is not None and found["candidates"] == 2

    def test_display_is_kept_out_of_the_merged_record(self, header_html: str) -> None:
        from bama_deep.ad_html import parse_ad_html

        page = parse_ad_html(header_html, now_ts=0)
        assert page["display_price"]["amount_text"] == "5,300,000,000"
        assert not any(k.startswith("price_display") for k in page["fields"])


# ---------------------------------------------------------------------------
# realistic adapter path: API + HTML + merge + display
# ---------------------------------------------------------------------------


class TestAdapterCorroboration:
    @pytest.mark.parametrize("api_fixture", ["api_detail_personal.json", "api_detail_dealer.json"])
    def test_typed_cash_plus_matching_display_qualifies(
        self, header_html: str, api_fixture: str
    ) -> None:
        decision = assess_detail(adapter_fields(header_html, api_payload(api_fixture)))
        assert decision.status == "qualified", decision.as_dict()
        assert decision.price_toman == 5_300_000_000
        assert decision.evidence["price"]["corroborated_by"] == ["api_price"]

    def test_json_ld_irr_label_neither_divides_nor_overrides(self, header_html: str) -> None:
        # The fixture's JSON-LD offer says 5300000000 IRR, as observed live.
        assert '"price":5300000000,"priceCurrency":"IRR"' in header_html
        decision = assess_detail(adapter_fields(header_html, api_payload()))
        assert decision.price_toman == 5_300_000_000  # not 530,000,000

    def test_display_that_disagrees_with_api_is_unknown(self, header_html: str) -> None:
        decision = assess_detail(adapter_fields(header_html, api_payload(price="5,350,000,000")))
        assert (decision.status, decision.reason) == (UNKNOWN, "amount_conflict")

    def test_no_api_means_no_corroboration(self, header_html: str) -> None:
        decision = assess_detail(adapter_fields(header_html, None))
        assert decision.status == UNKNOWN
        assert decision.reason == "display_without_typed_cash_amount"

    def test_api_without_header_display_is_unproven(self) -> None:
        html = (DEEP_FIXTURES / "html_detail_full.html").read_text(encoding="utf-8")
        html = html.replace('"negotiable"', '"lumpsum"', 1)
        decision = assess_detail(adapter_fields(html, api_payload()))
        assert (decision.status, decision.reason) == (UNKNOWN, "price_unit_unproven")

    def test_installment_listing_with_a_toman_header_is_not_a_full_ask(
        self, header_html: str
    ) -> None:
        payload = api_payload("api_detail_installment.json", price="0")
        decision = assess_detail(adapter_fields(header_html, payload))
        assert decision.status == UNKNOWN

    def test_presale_lumpsum_is_unsupported(self, header_html: str) -> None:
        payload = api_payload("api_detail_presale.json", drop_presale=False)
        decision = assess_detail(adapter_fields(header_html, payload))
        assert (decision.status, decision.reason) == (UNKNOWN, "presale_unsupported")

    def test_absent_is_pre_sale_does_not_reject_typed_cash(self, header_html: str) -> None:
        payload = api_payload()
        assert "is_pre_sale" not in payload["data"]["detail"]
        assert assess_detail(adapter_fields(header_html, payload)).status == QUALIFIED

    def test_explicit_not_presale_is_also_fine(self, header_html: str) -> None:
        payload = api_payload(drop_presale=False)
        assert payload["data"]["detail"]["is_pre_sale"] is False
        assert assess_detail(adapter_fields(header_html, payload)).status == QUALIFIED


# ---------------------------------------------------------------------------
# retained production fixtures fail closed
# ---------------------------------------------------------------------------


class TestRetainedProductionShapes:
    @pytest.mark.parametrize(
        ("name", "reason"),
        [
            ("api_detail_personal.json", "price_unit_unproven"),
            ("api_detail_dealer.json", "price_unit_unproven"),
            ("api_detail_phone_present.json", "price_unit_unproven"),
            ("api_detail_installment.json", "installment_offer_not_full_ask"),
            ("api_detail_presale.json", "presale_unsupported"),
            ("api_detail_empty.json", "price_type_unknown"),
        ],
    )
    def test_api_only_detail(self, name: str, reason: str) -> None:
        from bama_deep.ad_api import parse_ad_api
        from bama_deep.merge import merge_ad

        payload = json.loads((DEEP_FIXTURES / name).read_text(encoding="utf-8"))
        record = merge_ad(parse_ad_api(payload, now_ts=0), None, ad_id="a", url="u").record
        decision = assess_detail(record)
        assert (decision.status, decision.reason) == (UNKNOWN, reason)
        assert decision.price_toman is None

    def test_exact_lower_bound_bare_api_price_is_not_certified(self) -> None:
        # The original study's minimum observed price was exactly 1bn, unitless.
        from bama_deep.ad_api import parse_ad_api
        from bama_deep.merge import merge_ad

        payload = json.loads((DEEP_FIXTURES / "api_detail_phone_present.json").read_text())
        assert payload["data"]["price"]["price"] == "1,000,000,000"
        record = merge_ad(parse_ad_api(payload, now_ts=0), None, ad_id="a", url="u").record
        assert assess_detail(record).status == UNKNOWN

    def test_negotiable_html_with_currency_but_no_amount(self) -> None:
        from bama_deep.ad_html import parse_ad_html
        from bama_deep.merge import merge_ad

        html = (DEEP_FIXTURES / "html_detail_full.html").read_text(encoding="utf-8")
        page = parse_ad_html(html, now_ts=0)
        record = merge_ad(None, page, ad_id="a", url="u").record
        assert '"priceCurrency": "IRR"' in record["json_ld_json"]
        decision = assess_detail({**record, **display_price_fields(page)})
        assert (decision.status, decision.reason) == (UNKNOWN, "negotiable_without_asking_price")

    def test_base_model_irt_default_is_not_evidence(self) -> None:
        decision = assess_detail(
            {
                "price_currency": "IRT",
                "price_toman": 5_300_000_000,
                "price_type": "lumpsum",
                "year_text": "1403",
                "body_type": "passenger_car",
            }
        )
        assert (decision.status, decision.reason) == (UNKNOWN, "price_unit_unproven")

    def test_search_cards(self) -> None:
        body = json.loads((BASE_FIXTURES / "search_page0.json").read_text(encoding="utf-8"))
        reasons = {}
        for i, entry in enumerate(body["data"]["ads"]):
            ad = card_to_ad(entry, "https://bama.ir/car", position=i, cycle=0)
            if ad is None:
                continue
            assert ad.price_unit is None  # the API string carries no unit
            assert ad.body_type == "passenger_car"
            assert ad.ad_id == entry["detail"]["code"]
            assert ad.price_type not in reasons
            reasons[ad.price_type] = assess_card(_card_from_ad(ad)).reason
        assert reasons == {
            "negotiable": "negotiable_without_asking_price",
            "installment": "installment_offer_not_full_ask",  # positive 15,120,000,000
            "lumpsum": "price_unit_unproven",
        }


def _card_from_ad(ad: DiscoveredAd, **overrides: Any) -> DiscoveredCard:
    values: dict[str, Any] = {
        "platform_ad_id": ad.ad_id,
        "canonical_url": ad.url,
        "card_price_raw": ad.price_text,
        "card_year": ad.year_text,
        "card_price_type": ad.price_type,
        "card_price_unit": ad.price_unit,
        "card_body_type": ad.body_type,
        "card_is_pre_sale": ad.is_pre_sale,
    }
    values.update(overrides)
    return DiscoveredCard(**values)


# ---------------------------------------------------------------------------
# the rule: threshold, years, body, scope
# ---------------------------------------------------------------------------


class TestThreshold:
    @pytest.mark.parametrize(
        ("amount", "status"),
        [
            (PRICE_THRESHOLD_TOMAN - 1, PRICE_EXIT),
            (PRICE_THRESHOLD_TOMAN, PRICE_EXIT),  # equality is an exit
            (PRICE_THRESHOLD_TOMAN + 1, QUALIFIED),
            (1, PRICE_EXIT),
        ],
    )
    def test_boundary(self, amount: int, status: EligibilityStatus) -> None:
        decision = assess_detail(at(amount))
        assert decision.status == status
        assert decision.price_toman == amount

    def test_persian_digit_display_matches_ascii_api(self) -> None:
        decision = assess_detail(detail_fields(display="۱٬۰۰۰٬۰۰۰٬۰۰۱", price="1,000,000,001"))
        assert (decision.status, decision.price_toman) == (QUALIFIED, 1_000_000_001)

    def test_zero_is_never_a_price(self) -> None:
        decision = assess_detail(detail_fields(display="0", price="0"))
        assert (decision.status, decision.reason) == (UNKNOWN, "zero_price")

    @pytest.mark.parametrize(
        ("display", "unit", "reason"),
        [
            ("53,000,000,000", "ریال", "rial_amount_unsupported@html_header"),
            ("5,300,000,000", "تومن", "malformed_display@html_header"),
            ("5,300,000,000", "USD", "malformed_display@html_header"),
            ("5.3", "تومان", "malformed_display@html_header"),
            ("-5,300,000,000", "تومان", "malformed_display@html_header"),
            ("5,300,000,000", None, "incomplete_display@html_header"),
        ],
    )
    def test_unproven_or_malformed_display(self, display: str, unit: Any, reason: str) -> None:
        decision = assess_detail(detail_fields(display=display, unit=unit))
        assert (decision.status, decision.reason) == (UNKNOWN, reason)

    @pytest.mark.parametrize("price", ["5,300,000,000.0", "5.300.000.000", "-5300000000", "۵,300"])
    def test_malformed_api_amount(self, price: str) -> None:
        decision = assess_detail(detail_fields(price=price))
        assert (decision.status, decision.reason) == (UNKNOWN, "malformed_amount@api_price")

    @pytest.mark.parametrize(
        ("price_type", "reason"),
        [
            ("installment", "installment_offer_not_full_ask"),
            ("negotiable", "negotiable_without_asking_price"),
            ("hidden", "price_hidden"),
            ("notShown", "price_hidden"),
            ("exchange", "unsupported_price_type:exchange"),
            (None, "price_type_unknown"),
        ],
    )
    def test_offer_type(self, price_type: Any, reason: str) -> None:
        decision = assess_detail(detail_fields(price_type=price_type))
        assert (decision.status, decision.reason) == (UNKNOWN, reason)

    def test_deposit_terms_on_a_lumpsum_offer(self) -> None:
        fields = detail_fields(**{"price.prepayment_primary": "1,010,000,000"})
        assert assess_detail(fields).reason == "installment_terms_on_offer"

    def test_stale_record_is_unknown(self) -> None:
        fields = {**at(5_000_000_000), "observed_fresh": False}
        assert assess_detail(fields).reason == "evidence_not_from_current_observation"


class TestYearAndBody:
    @pytest.mark.parametrize(
        ("year", "status"),
        [
            ("1397", QUALIFIED),
            ("۱۳۹۷", QUALIFIED),
            ("1396", OUT),
            ("2018", QUALIFIED),
            ("2017", OUT),
            ("1450", QUALIFIED),
            (1403, QUALIFIED),
            ("1,397", UNKNOWN),
            ("13۹7", UNKNOWN),  # mixed scripts
            ("97", UNKNOWN),
            ("1397-2018", UNKNOWN),
            (None, UNKNOWN),
        ],
    )
    def test_native_calendar_floor(self, year: Any, status: EligibilityStatus) -> None:
        assert assess_detail(at(2_000_000_000, year=year)).status == status

    def test_years_are_not_converted(self) -> None:
        assert native_year("2018") == ("gregorian", 2018)
        assert native_year("1397") == ("jalali", 1397)

    def test_disagreeing_years_are_unknown(self) -> None:
        fields = {**at(2_000_000_000, year="1397"), "year_text": "2018"}
        assert assess_detail(fields).reason.startswith("year_unconfirmed:conflict")

    def test_out_of_scope_year_wins_over_price_exit(self) -> None:
        assert assess_detail(at(900_000_000, year="1390")).status == OUT

    def test_price_exit_does_not_need_scope_confirmation(self) -> None:
        assert assess_detail(at(900_000_000, year=None, body=None)).status == PRICE_EXIT

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ("passenger_car", QUALIFIED),
            ("pickup", OUT),
            ("heavy_vehicle", OUT),
            ("سواری", UNKNOWN),
            (None, UNKNOWN),
        ],
    )
    def test_body(self, body: Any, status: EligibilityStatus) -> None:
        assert assess_detail(at(2_000_000_000, body=body)).status == status

    def test_body_conflict_is_unknown(self) -> None:
        fields = {**at(2_000_000_000), "body_type": "pickup"}
        assert assess_detail(fields).status == UNKNOWN

    @pytest.mark.parametrize("origin", ["iranian", "foreign", "chinese", None])
    @pytest.mark.parametrize("seller", ["personal", "nonpersonal", None])
    def test_origin_and_seller_type_are_unbounded(self, origin: Any, seller: Any) -> None:
        fields = {**at(2_000_000_000), "manufacturer_country": origin, "seller_type": seller}
        assert assess_detail(fields).status == QUALIFIED


class TestCards:
    def _card(self, **kw: Any) -> DiscoveredCard:
        values: dict[str, Any] = {
            "platform_ad_id": "c1",
            "canonical_url": "https://bama.ir/car/detail-c1",
            "card_price_raw": "2,000,000,000",
            "card_price_type": "lumpsum",
            "card_year": "1401",
            "card_body_type": "passenger_car",
        }
        values.update(kw)
        return DiscoveredCard(**values)

    def test_typed_bare_card_is_unproven(self) -> None:
        assert assess_card(self._card()).reason == "price_unit_unproven"

    def test_unit_marked_card_text_still_needs_a_typed_raw_amount(self) -> None:
        card = self._card(card_price_raw="2,000,000,000 تومان", card_price_unit="toman")
        assert assess_card(card).reason == "display_without_typed_cash_amount"

    def test_dom_card_has_no_offer_type(self) -> None:
        ad = _dom_card_to_ad(
            {"href": "/car/detail-abc12345-x", "lines": ["x", "۲٬۰۰۰٬۰۰۰٬۰۰۰ تومان", "1401"]},
            "https://bama.ir/car",
            0,
        )
        assert ad is not None and (ad.price_unit, ad.price_unit_source) == (
            "toman",
            "dom_card_text",
        )
        assert assess_card(_card_from_ad(ad)).reason == "price_type_unknown"

    def test_unit_label_without_marked_text_is_rejected(self) -> None:
        card = self._card(card_price_unit="toman")
        assert assess_card(card).reason == "unit_label_without_marked_amount@card"

    def test_scope_decisions_work_from_cards(self) -> None:
        assert assess_card(self._card(card_year="1396")).status == OUT
        assert assess_card(self._card(card_body_type="pickup")).status == OUT

    def test_stale_card_is_unknown(self) -> None:
        card = self._card(card_year="1396", card_evidence_current=False)
        assert assess_card(card).reason == "evidence_not_from_current_observation"

    def test_presale_card_is_unsupported(self) -> None:
        card = self._card(card_price_raw="2,000,000,000 تومان", card_is_pre_sale=True)
        assert assess_card(card).reason == "presale_unsupported"

    def test_card_hash_ignores_evidence_fields(self) -> None:
        plain = self._card()
        rich = self._card(card_price_unit=None, card_observation_id="x", card_evidence_current=True)
        assert plain.card_hash() == rich.card_hash()


# ---------------------------------------------------------------------------
# scratch database: no lending across observations
# ---------------------------------------------------------------------------


def _entry(code: str, price: str, price_type: str, year: str = "1401") -> dict[str, Any]:
    body = json.loads((BASE_FIXTURES / "search_page0.json").read_text(encoding="utf-8"))
    template = next(e for e in body["data"]["ads"] if e.get("type") == "ad")
    entry = copy.deepcopy(template)
    entry["detail"]["code"] = code
    entry["detail"]["url"] = f"/car/detail-{code}-x"
    entry["detail"]["year"] = year
    entry["price"]["price"] = price
    entry["price"]["type"] = price_type
    return entry


def _pages(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"data": {"ads": entries}}, {"data": {"ads": []}}, {"data": {"ads": []}}]


class _ProfileConfig(MonitorConfig):
    study_profile: str = "legacy"


def _run_discovery(
    monkeypatch: pytest.MonkeyPatch, evidence: Path, entries: list[dict[str, Any]], profile: str
) -> list[DiscoveredCard]:
    import asyncio

    import bama_scraper.discovery as discovery

    pages = _pages(entries)
    monkeypatch.setattr(discovery, "_fetch_page", lambda client, params, page: pages[page])
    cfg = _ProfileConfig(
        study_profile=profile,
        output_dir=evidence.parent,
        detail=DetailPolicy(delay_min=0.0, delay_max=0.0),
    )
    result = asyncio.run(BamaDiscoveryScraper(cfg, evidence).discover("https://bama.ir/car"))
    return result.cards


class TestScratchDatabase:
    def test_resumed_pass_does_not_lend_type_price_or_presence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        evidence = tmp_path / "runs" / "run_1"
        first = [
            _entry("aaaa1111", "2,000,000,000", "lumpsum"),
            _entry("bbbb2222", "1,500,000,000", "lumpsum"),
        ]
        _run_discovery(monkeypatch, evidence, first, NATIONWIDE_PROFILE)
        # The same slot resumes: A turned negotiable, B is gone from the search.
        second = [_entry("aaaa1111", "0", "negotiable")]
        cards = _run_discovery(monkeypatch, evidence, second, NATIONWIDE_PROFILE)

        assert [c.platform_ad_id for c in cards] == ["aaaa1111"]
        (card,) = cards
        assert card.card_price_type == "negotiable"
        assert card.card_price_raw == "0"
        assert card.card_price_normalized is None  # not the earlier 2,000,000,000
        assert card.card_evidence_current is True
        assert assess_card(card).reason == "negotiable_without_asking_price"

    def test_legacy_profile_keeps_legacy_rows_and_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        evidence = tmp_path / "runs" / "run_1"
        first = [
            _entry("aaaa1111", "2,000,000,000", "lumpsum"),
            _entry("bbbb2222", "1,500,000,000", "lumpsum"),
        ]
        _run_discovery(monkeypatch, evidence, first, "legacy")
        cards = _run_discovery(
            monkeypatch, evidence, [_entry("aaaa1111", "0", "negotiable")], "legacy"
        )

        by_id = {c.platform_ad_id: c for c in cards}
        assert set(by_id) == {"aaaa1111", "bbbb2222"}  # legacy read-back, unchanged
        # Legacy COALESCE semantics are preserved in the legacy fields ...
        assert by_id["aaaa1111"].card_price_normalized == 2_000_000_000
        # ... while the evidence fields still describe only the current sighting.
        assert by_id["aaaa1111"].card_price_type == "negotiable"
        assert by_id["bbbb2222"].card_evidence_current is False
        assert assess_card(by_id["bbbb2222"]).status == UNKNOWN

    def test_storage_observation_rows_are_replaced_not_coalesced(self, tmp_path: Path) -> None:
        def ad(price: str | None, price_type: str | None, body: str | None) -> DiscoveredAd:
            return DiscoveredAd(
                ad_id="a1",
                url="https://bama.ir/car/detail-a1",
                source_search_url="s",
                price_text=price,
                price_type=price_type,
                body_type=body,
                year_text="1401",
            )

        with Storage(tmp_path / "s.sqlite") as storage:
            storage.upsert_discovered([ad("2,000,000,000", "lumpsum", "passenger_car")], "r1")
            storage.upsert_discovered([ad(None, None, None)], "r2")
            legacy = storage.conn.execute(
                "SELECT price_text, price_type FROM discovered_ads"
            ).fetchone()
            obs = storage.conn.execute(
                "SELECT observation_id, price_text, price_type, body_type FROM discovery_observations"
            ).fetchone()
        assert tuple(legacy) == ("2,000,000,000", "lumpsum")  # legacy behaviour kept
        assert tuple(obs) == ("r2", None, None, None)  # current sighting only


# ---------------------------------------------------------------------------
# detail adapter with an offline fake fetcher
# ---------------------------------------------------------------------------


class _FakeFetcher:
    def __init__(self, payload: dict[str, Any], html: str | Exception) -> None:
        self.payload = payload
        self.html = html
        self.calls: list[str] = []

    async def fetch_json(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        return self.payload

    async def fetch_html(self, url: str) -> str:
        self.calls.append(url)
        if isinstance(self.html, Exception):
            raise self.html
        return self.html


def _scrape(profile: str, fetcher: _FakeFetcher) -> Any:
    import asyncio

    from bama_monitor.scrapers import BamaDetailScraper

    scraper = BamaDetailScraper(_ProfileConfig(study_profile=profile))
    scraper._fetcher = fetcher
    return asyncio.run(scraper.scrape("https://example.invalid/car/detail-demo0001-x"))


class TestDetailAdapter:
    def test_html_block_after_api_success_is_blocked(self) -> None:
        from bama_deep.net import BlockedError

        fetcher = _FakeFetcher(api_payload(), BlockedError("HTTP 403"))
        result = _scrape(NATIONWIDE_PROFILE, fetcher)
        assert len(fetcher.calls) == 2  # the API answered, then the page was denied
        assert str(result.verdict) == "blocked_response"
        assert result.ok is False
        assert result.fields == {}
        assert assess_detail(result.fields).status == UNKNOWN

    def test_legacy_profile_keeps_its_html_block_behaviour(self) -> None:
        from bama_deep.net import BlockedError

        result = _scrape("legacy", _FakeFetcher(api_payload(), BlockedError("HTTP 403")))
        assert str(result.verdict) == "detail_page_still_active"
        assert result.ok is True

    def test_cash_listing_end_to_end(self, header_html: str) -> None:
        from bama_monitor.scrapers import _content_hash

        result = _scrape(NATIONWIDE_PROFILE, _FakeFetcher(api_payload(), header_html))
        assert result.fields["price_display_amount_text"] == "5,300,000,000"
        decision = assess_detail(result.fields)
        assert (decision.status, decision.price_toman) == (QUALIFIED, 5_300_000_000)
        # The display evidence does not perturb the legacy change-detection hash.
        record = {k: v for k, v in result.fields.items() if not k.startswith("price_display")}
        assert result.content_hash == _content_hash(record)
