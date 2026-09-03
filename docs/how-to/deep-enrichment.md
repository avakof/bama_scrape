# Run deep enrichment

The deep scraper starts from the base scraper's discovered-ad inventory and builds a
separate, resumable SQLite dataset with cross-source advertisement fields, technical
specifications, price series, and dealer information.

## Prerequisites

Complete base discovery first so `output/bama_ads.sqlite` exists. The parent editable
install supplies shared normalization and fetching code.

```bash
pip install -e .
cp deep_scraper/config.example.yaml deep_scraper/config.yaml
```

The canonical entrypoint is:

```bash
python deep_scraper/run_deep.py --config deep_scraper/config.yaml <command>
```

## Seed without modifying the source

```bash
python deep_scraper/run_deep.py --config deep_scraper/config.yaml seed
```

The source SQLite database is opened read-only. `--only-completed` restricts the seed
to advertisements completed by the base run; the default retains the complete
discovery ledger.

## Plan before requesting

```bash
python deep_scraper/run_deep.py --config deep_scraper/config.yaml plan
```

`plan` prints pending entities, requests per entity, phase estimates, and the
configured fetch ceiling without issuing a network request. The command still opens
the deep database, creates its directories, and records a run row.

The ceiling counts successful fetches, not every wire attempt. Transport retries and
failed responses are not added to `requests_issued`, so `max_requests` is not a
strict upper bound on HTTP attempts. Combine the estimate with conservative delays,
phase-by-phase operation, and observation of blocking responses.

`--dry-run` is **not** a repository-wide no-network guard: the current CLI honors it
only for the standalone `ads` command. Do not use `run --dry-run`,
`specs --dry-run`, `prices --dry-run`, or `dealers --dry-run` as a safety control.
Use `plan` when no request is permitted.

## Run by phase

```bash
python deep_scraper/run_deep.py --config deep_scraper/config.yaml ads --limit 5
python deep_scraper/run_deep.py --config deep_scraper/config.yaml ads
python deep_scraper/run_deep.py --config deep_scraper/config.yaml specs
python deep_scraper/run_deep.py --config deep_scraper/config.yaml prices
python deep_scraper/run_deep.py --config deep_scraper/config.yaml dealers
```

Phase A combines the public advertisement API and HTML view, scrubs both, merges
them, and records critical disagreements. Phases B–D are keyed by review, price, and
dealer identifiers learned during phase A. An all-in-one `run` shares one fetcher
across its phases, enforcing one semaphore and successful-fetch counter.

The merge does not assume one source wins every field. Typed price, mileage, and year
values, display strings, API-only attributes, HTML-only attributes, and JSON-LD
condition evidence have declared precedence. `provenance_json` records the selected
source per field and `ad_field_conflicts` retains disagreements instead of silently
discarding one value.

Some Bama numeric fields have a display string and a compact integer whose implied
decimal scale varies with the string. The deep schema therefore retains `*_text`, a
parsed numeric value, the raw integer where available, and a reconciliation result.
Do not reinterpret the raw integer alone as a physical unit.

Use `--skip-api` or `--skip-html` only when accepting lower cross-validation coverage.
Use `--retry-delisted` when intentionally rechecking terminal delisted records and
`--refresh` for a new fixed snapshot of completed entities.

`--limit` on `run` caps phase A advertisement work; it does not cap every later
enrichment phase. Run phases separately when each phase needs its own bounded sample.

Queue state is resumable. `fetching` rows remain recoverable after interruption;
completed rows are skipped unless refreshed; 404/410 results become `delisted`; and a
one-source success can be stored as `partial`. Blocking responses stop work rather
than triggering bypass or aggressive retry behavior.

## Audit and export

```bash
python deep_scraper/run_deep.py --config deep_scraper/config.yaml reindex
python deep_scraper/run_deep.py --config deep_scraper/config.yaml audit
python deep_scraper/run_deep.py --config deep_scraper/config.yaml stats
python deep_scraper/run_deep.py --config deep_scraper/config.yaml export
```

`reindex` rebuilds the specification catalog and generated wide view. The audit checks
completion, critical conflicts, referential integrity, numeric reconciliation, and
privacy. `--strict` makes any warning fail the audit.

`run` performs seed, enabled phases, audit, and export end to end:

```bash
python deep_scraper/run_deep.py --config deep_scraper/config.yaml run
```

## Privacy options

Known contact keys and free-text contact patterns are removed before data is stored.
Dealer addresses are public business-premises data; omit the export column with
`--no-dealer-address`. Pattern redaction cannot promise coverage of every novel
format, so the audit remains a gate and outputs still need review.

Image URLs are recorded. Although `download_images` and `--download-images` are
accepted, the current deep implementation does not download image bytes. The
`raw_description` configuration field is also currently inert.

`deep_scraper/data/bama_deep.sqlite` is the source of truth. The EDA can join it only
through explicit, provenanced enrichment. CSV, JSONL, Parquet, generated wide-spec
views, and `bama_deep_snapshot.sqlite` are derivatives produced by `export`.

See [Project status and known limitations](../reference/project-status-and-limitations.md#accepted-but-incomplete-options)
before relying on optional flags.
