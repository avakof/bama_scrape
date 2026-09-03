# Collect a one-time dataset

Use the base scraper when you need a bounded snapshot of one Bama search and its
public advertisement details.

## Configure the target

```bash
cp config.example.yaml config.yaml
```

Edit `url` without changing its meaning. Bama's public URL parameters can encode
lower bounds rather than exact values; record the original URL with every run.
Keep `api_page_size` at 30 or below and begin with the conservative concurrency and
delay defaults.

For the repository's reference URL, the dated observation interpreted
`year=1397-2018,` as an open-ended lower year bound and `price=1000000000` as a
minimum asking price—not an exact year or maximum price. Re-run `inspect` and review
the current site before generalizing that mapping to another search.

`--config` is a top-level option:

```bash
python -m bama_scraper --config config.yaml <command>
```

CLI flags cover the common operational fields. Browser identity, timeouts, API page
size, retry count, context recycling, and failed-HTML policy are configuration-only
fields; see [`config.example.yaml`](../../config.example.yaml).

## Inspect when behavior is uncertain

`inspect` launches Playwright, records network candidates, and saves evidence about
the delivery mechanism.

```bash
playwright install chromium
python -m bama_scraper --config config.yaml inspect --headed
```

Inspection is diagnostic. The default `auto` discovery mode uses plain HTTP after a
JSON endpoint has been confirmed and falls back to browser scrolling if necessary.

## Run the stages

Run stages independently when you want to inspect progress:

```bash
python -m bama_scraper --config config.yaml discover
python -m bama_scraper --config config.yaml scrape-details --limit 5
python -m bama_scraper --config config.yaml scrape-details --resume
python -m bama_scraper --config config.yaml validate
python -m bama_scraper --config config.yaml export
```

Omit `--limit` for the full detail inventory. `--resume` skips completed work; the
configuration default already enables it. Use `--refresh` only when you intend to
refetch completed records.

For a full pass:

```bash
python -m bama_scraper --config config.yaml run --resume
```

That command performs discovery, detail collection, a verification pass, export,
and the adversarial audit.

## Resume and failure states

SQLite is the checkpoint. Advertisement work moves through these states:

```text
discovered -> pending -> scraping -> completed
                              |-> retryable_error
                              `-> permanent_error
```

- Completed rows are skipped when resume is enabled unless `--refresh` is used.
- Retryable rows remain claimable until the configured attempt limit.
- Rows left in `scraping` by an interrupted process are recovered for a later run.
- HTTP 404/410 is terminal for that observed URL and remains in the error ledger.
- A block response stops the run; work that was not issued should not consume an
  advertisement retry.
- Progress is committed incrementally. `Ctrl+C` returns exit code 130 after the
  current cleanup path and retains the database checkpoint.

## Understand termination

API discovery stops after `api_stale_pages` consecutive pages with zero real
advertisements. It records Bama's metadata but does not trust mutable total counters
as the stopping rule.

Scroll discovery extracts every cycle because old cards can be removed from the DOM.
It stops after `stale_cycles` consecutive cycles without a new canonical ad and also
honors `max_scrolls` and `max_runtime` ceilings.

A live marketplace changes during collection. Completeness means the bounded walk
stabilized under the recorded rules—not that the market stood still.

## Validate offline

The regular validation command performs a second discovery pass. To audit only the
stored data:

```bash
python -m bama_scraper --config config.yaml validate --skip-second-pass
```

Review `output/run_summary.json`, especially termination, discovery stabilization,
errors, filter-bound checks, ID/URL consistency, and privacy findings. A 404 or 410
between discovery and detail collection is an observed delisting and should remain in
the error ledger.

The base privacy finding only proves that known contact **keys** are absent. It does
not scan arbitrary seller-written description text for phone numbers, email
addresses, or messaging handles. Do not publish base exports without a separate
free-text scrub and review.

## Backfill derived fields

When a pure normalizer improves, recompute values from persisted raw text instead of
requesting the site again:

```bash
python -m bama_scraper --config config.yaml backfill
```

Relative publication phrases are anchored to the original `scraped_at` time. Add
`--refresh` to recompute all eligible rows rather than only missing values.

## Output ownership

`output/bama_ads.sqlite` is authoritative. CSV, JSONL, Parquet, and SQLite-copy files
are replaceable exports. Debug HTML and screenshots contain captured public page
content and must not be committed.

See [Data and outputs](../reference/data-and-outputs.md#base-scraper) and
[Troubleshooting](../reference/troubleshooting.md#base-scraper).
