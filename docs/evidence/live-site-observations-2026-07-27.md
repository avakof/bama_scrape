# Live-site observations — 2026-07-27

> Historical evidence. Bama's inventory, endpoints, frontend, policies, and response
> metadata can change. Re-run `inspect` before relying on these details operationally.

## Target

The observation used this saved search:

```text
https://bama.ir/car?year=1397-2018,&price=1000000000&body=passenger_car&country=iranian
```

The URL was interpreted as production year 1397 Jalali/2018 Gregorian or newer,
asking price at least 1,000,000,000 Toman, passenger-car body, and Iranian country
classification. The trailing comma left the upper year bound open.

## Delivery behavior observed

The initial Nuxt page contained search cards, then requested the public
`/cad/api/search` JSON endpoint with page-index and page-size parameters. Values above
30 for page size returned HTTP 400. The response mixed advertisement and campaign
banner entries.

The endpoint's reported total count and total pages grew during pagination and could
overshoot the number of real advertisement rows. `has_next` changed at the boundary
but sat beside demonstrably unstable counters. This evidence motivated termination
after two consecutive observed pages containing zero real advertisements.

One recorded boundary sequence was:

```text
page=93  real_ads=30  total_count=2821  total_pages=95  has_next=True
page=94  real_ads=25  total_count=2845  total_pages=95  has_next=False
page=95  real_ads=0   total_count=2850  total_pages=95  has_next=False
page=96  real_ads=0   total_count=2880  total_pages=96  has_next=False
```

The live feed changed during repeated passes, so page 94's real-ad count was not
stable. That is another reason to record stabilization evidence rather than describe
one server counter as a timeless total.

The browser scroll path also showed virtualized cards: older DOM nodes were removed
as new ones appeared. This motivated extracting and deduplicating on every scroll
cycle rather than reading the DOM once at the end.

## Detail behavior observed

Advertisement HTML contained a devalue-serialized Nuxt payload and JSON-LD blocks.
The raw HTTP response was sufficient for normal detail parsing; Playwright remained a
fallback. A partially masked phone field could occur inside the payload and was
removed before storage.

Negotiable prices could carry a numeric-looking zero in the payload. The parser kept
price type separate so zero was not misrepresented as a real asking price.

## Recorded run

The dated run recorded these execution-specific results:

| Stage | Observed result |
|---|---|
| Discovery | 2,849 unique advertisements over 97 API pages in about 200 seconds; stopped after two consecutive empty pages |
| Detail collection | 2,846 completed, 9 permanent errors, 0 retryable errors, and 0 blocked; about 35 minutes at concurrency 2 |
| Verification | Three passes found 5 new, then 1 new, then 0 new advertisements; stabilized at 2,855 discovered |
| Final parsed dataset | 2,846 advertisements and 6,834 media rows |

All nine permanent detail errors were HTTP 410 responses for listings that
disappeared between discovery and detail collection. They remained in the error
ledger: 2,846 completed plus 9 gone equals the stabilized 2,855 discovery records.

Recorded field coverage was:

| Field | Coverage observed |
|---|---|
| Numeric asking price | 2,846 / 2,846; minimum 1,000,000,000 Toman |
| Images | 2,294 / 2,846 |
| Description | 2,461 / 2,846 |
| Publication date | 2,846 / 2,846; range 2025-11-09 through 2026-07-27 |
| Full Nuxt-payload parse | 2,846 / 2,846; no partial parses |
| Dedicated `phone` keys detected by the audit | 0 |

Integrity checks found no orphan detail rows, completed records without details,
unparseable canonical URLs, ID/URL mismatches, filter-bound violations, or persisted
phone keys.

That key-based audit did not search seller-written descriptions for arbitrary phone,
email, or messaging-handle text. The result must not be interpreted as proof that
the base dataset contained no incidental contact information.

These figures describe this target during this execution window, not a permanent
inventory size, coverage rate, or performance guarantee. The original run artifacts,
where retained locally, remain the source for exact provenance.

## What this did not establish

- That the same endpoint or payload structure still exists.
- That every Bama search uses the same delivery path.
- That an empty or unavailable page means a vehicle sold.
- That current terms or law permit a new collection.
- That a result count can be reproduced after the live market changes.
- That deeper pages beyond the observed boundary contain no transient items forever.

Current collection behavior is documented in [Collect a dataset](../how-to/collect.md)
and guarded by offline termination and parser tests.
