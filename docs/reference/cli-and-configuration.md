# CLI and configuration reference

The command parsers are authoritative. Use `<entrypoint> <command> --help` for the
exact options accepted by the installed revision.

## Base scraper

Entry point: `python -m bama_scraper [--config FILE] <command>`.

<!-- command-set:bama_scraper -->
| Command | Behavior |
|---|---|
| `inspect` | Observe the live page and record network candidates |
| `discover` | Enumerate and persist advertisements from the search |
| `scrape-details` | Fetch and parse persisted discovery items |
| `run` | Discover, scrape details, verify, export, and audit |
| `export` | Rebuild CSV, JSONL, Parquet, and SQLite exports |
| `validate` | Run completeness and integrity checks |
| `backfill` | Recompute derived fields from persisted raw values |
<!-- /command-set:bama_scraper -->

Every command accepts `--url`, `--mode`, `--headed`/`--headless`, `--max-scrolls`,
`--stale-cycles`, `--max-runtime`, `--concurrency`, `--delay-min`, `--delay-max`,
`--resume`, `--refresh`, `--download-images`, `--output-dir`, and `--log-level`.
`scrape-details` adds `--limit`; `validate` adds `--skip-second-pass`.

Options not present on the command line—including user agent, viewport, page size,
request/navigation timeouts, retry count, context recycling, and failed-HTML
policy—must be set in YAML. `--resume`, `--refresh`, and `--download-images` are
enable-only switches; use YAML when an explicit false value is needed.

Configuration: [`config.example.yaml`](../../config.example.yaml).

| Group | Keys |
|---|---|
| Target | `url`, `mode` |
| Browser | `headed`, `user_agent`, `locale`, `viewport_width`, `viewport_height` |
| Discovery | `max_scrolls`, `stale_cycles`, `api_stale_pages`, `max_runtime`, `scroll_pause_ms`, `settle_ms`, `api_page_size` |
| Details | `concurrency`, `delay_min`, `delay_max`, `max_retries`, `request_timeout`, `navigation_timeout`, `recycle_context_every` |
| Behavior | `resume`, `refresh`, `download_images` |
| Output | `output_dir`, `log_level`, `save_failed_html` |

`mode` is `auto`, `api`, `pagination`, or `scroll`. The API rejects page sizes above
30. Unknown YAML keys fail validation.

## Deep scraper

Entry point: `python deep_scraper/run_deep.py [--config FILE] <command>`.

<!-- command-set:bama_deep -->
| Command | Behavior |
|---|---|
| `seed` | Import discovery inventory from the base SQLite database read-only |
| `ads` | Phase A advertisement API and HTML collection |
| `specs` | Phase B trim reviews and technical specifications |
| `prices` | Phase C price keys and history |
| `dealers` | Phase D dealer profiles and inventories |
| `run` | Run seed, enabled phases, audit, and export |
| `audit` | Check completion, privacy, and cross-source conflicts |
| `export` | Write portable exports and a SQLite snapshot |
| `reindex` | Rebuild the specification catalog and wide view |
| `stats` | Print database and phase status |
| `plan` | Estimate the request budget without network activity |
<!-- /command-set:bama_deep -->

All commands accept `--source-db`, `--output-dir`, request-limit and concurrency
options, `--phases`, `--limit`, resume/refresh controls, source-skipping options,
`--download-images`, `--dry-run`, `--no-dealer-address`, and `--log-level`. `seed`
adds `--only-completed`; `audit` adds `--strict`.

Configuration: [`deep_scraper/config.example.yaml`](../../deep_scraper/config.example.yaml).

| Group | Keys |
|---|---|
| I/O | `source_db`, `output_dir` |
| Requests | `concurrency`, `delay_min`, `delay_max`, `max_retries`, `request_timeout`, `recycle_context_every`, `user_agent`, `referer` |
| Bounds | `max_runtime`, `max_requests` |
| Behavior | `phases`, `skip_api`, `skip_html`, `refresh`, `retry_delisted`, `download_images`, `dry_run` |
| Audit/export | `max_critical_conflict_rate`, `raw_description`, `no_dealer_address` |
| Logging | `log_level`, `json_logs` |

Phase letters are `a`, `b`, `c`, and `d`. One request ceiling and semaphore are shared
across the selected phases of an all-in-one `run`. The current counter advances only
after successful responses, so `max_requests` is not a strict cap on transport
attempts: retries and failed requests can exceed it.

Important current option boundaries:

- `plan` is the network-free request estimator.
- `--dry-run` short-circuits standalone `ads` only; it does not guard `run`,
  `specs`, `prices`, or `dealers`.
- `run --limit N` limits phase A only.
- `download_images`/`--download-images` is accepted but no deep phase currently
  downloads image bytes.
- `raw_description` is accepted in YAML but is not consumed by the exporter.
- `json_logs` is accepted in YAML but the deep CLI currently configures logging
  with `log_level` only.

## Monitor

Entry point: `python -m bama_monitor [--config FILE] <command>`.

<!-- command-set:bama_monitor -->
| Command | Behavior |
|---|---|
| `run-daily` | Execute the complete daily pipeline |
| `discover-only` | Persist discovery without applying comparison |
| `compare` | Report the stored comparison for a run |
| `verify-missing` | Check pages for advertisements absent from a run |
| `refresh-details` | Refresh advertisements due for a detail check |
| `detect-reposts` | Match recent removals with possible successor listings |
| `report` | Generate the daily report tree |
| `backfill` | List scheduled slot instants for a UTC date range; it does not create runs or reconstruct inventory |
| `validate` | Validate configuration, schedule, database, and scraper wiring |
| `migrate` | Apply pending database migrations |
| `analytics` | Print one analytical view as JSON |
| `status` | Summarize inventory and latest-run state |
| `validation-sample` | Draw a stratified disappearance-labeling worksheet |
| `validation-ingest` | Load completed human labels |
| `calibration` | Report observed outcomes by score band |
| `snapshot` | Validate and export one immutable daily snapshot |
| `history` | Describe the genuine-production boundary and earlier runs |
<!-- /command-set:bama_monitor -->

Common options are `--database-url`, `--search-url`, `--timezone`, `--run-at`,
`--removal-confirmation-misses`, `--output-dir`, `--reports-dir`, `--log-level`, and
`--dry-run`.

Command-specific options:

- `compare`, `verify-missing`, `report`, and `snapshot`: `--run-id`.
- `report`: `--date`.
- `snapshot`: `--validate-only`, `--allow-invalid`.
- `backfill`: required `--from-date` and `--to-date`.
- `run-daily`: `--scheduled-for`, `--trigger-type`, `--is-synthetic`,
  `--production-schedule-name`, `--export-snapshot`.
- `analytics`: `--view`, `--dimension`, `--limit`.
- `validation-sample`: `--per-band`, `--out`.
- `validation-ingest`: required `--worksheet`, optional `--labelled-by`.

`analytics --view` accepts `daily_inventory`, `time_to_disappearance`,
`fastest_disappearing`, `left_truncated_excluded`, `vehicle_durations`,
`filter_exits`, `truncation_summary`, `market`, `cohorts`, and `survival`.

Local configuration: [`config.monitor.example.yaml`](../../config.monitor.example.yaml).
The separate [`deploy/config.example.yaml`](../../deploy/config.example.yaml) is a
partial, container-oriented override and is not a safe native-systemd default.

| Group | Keys |
|---|---|
| Schedule/identity | `search_url`, `timezone`, `run_at`, `production_schedule_name`, `catch_up_grace_minutes`, `watchdog_interval_seconds`, `on_time_grace_minutes` |
| State policy | `removal_confirmation_misses`, `persistent_absence_days`, `entry_tolerance_hours` |
| Health | `health.*` count, page, parse, missing, termination, and stabilization thresholds |
| Sale inference | `scoring.*` evidence weights and label thresholds |
| Reposts | `repost.*` lookback, tolerances, signal weights, and match threshold |
| Detail policy | `detail.*` census/refresh policy, concurrency, delays, and cap |
| Alerts | `alerts.*` enabled state, severity, and environment-variable names |
| Runtime | `output_dir`, `daily_snapshot_dir`, `reports_dir`, `lock_dir`, `max_discovery_pages`, `max_run_seconds`, `dry_run`, `log_level` |

PostgreSQL uses dialect-aware migrations; SQLite remains supported for local work and
tests. Never run migrations from two processes concurrently.

The `backfill` command parses `--from-date` and `--to-date` as UTC midnights and
prints the schedule slots in that interval. It never fetches or creates a historical
run; past inventory is unavailable from the current site.

The scheduler daemon has its own operational entrypoint:

```bash
python -m bama_monitor.scheduler_daemon --help
python -m bama_monitor.scheduler_daemon --config config.monitor.yaml --print-next
```

It accepts database, time, timezone, and production-schedule overrides plus `--once`
and `--print-next`.

`dry_run`/`--dry-run` is accepted by monitor configuration and the normal parser,
but the current command pipeline does not consume it. It is not a no-network guard.
The current scoring path also hard-codes the seven-day persistence rule instead of
reading `persistent_absence_days`; repost fingerprinting does not read
`price_bucket_toman`; and refresh selection reads `active_refresh_days_max` but not
`active_refresh_days_min`.

## EDA

Entry point: `python -m bama_eda <command> [options]`.

<!-- command-set:bama_eda -->
| Command | Behavior |
|---|---|
| `inspect-schema` | Inventory tables, columns, roles, and relationships |
| `build-datasets` | Build and export the three analytical datasets |
| `validate` | Run data-integrity checks without charts |
| `run` | Build datasets, audit, statistics, charts, and report |
| `report` | Regenerate the full analysis and report |
| `compare-runs` | Compare two explicit monitoring inventories |
<!-- /command-set:bama_eda -->

Common options are `--database-url`, `--config`, `--output-dir`, and `--log-level`.
All commands except `compare-runs` accept `--run-id`. Dataset/report commands accept
sample and survival thresholds, snapshot staleness, left-truncation inclusion,
optional deep enrichment, and CSV/Parquet toggles. `run` and `report` add HTML and
chart toggles. `compare-runs` requires `--run-id-a` and `--run-id-b`.

Configuration: [`config.eda.example.yaml`](../../config.eda.example.yaml).

| Group | Keys |
|---|---|
| Selection/output | `run_id`, `output_dir`, `cache_dir`, `html`, `export_csv`, `export_parquet` |
| Reproducibility | `random_seed`, `max_rows_in_memory`, `chunk_size`, `log_level` |
| Interpretation | `include_left_truncated`, `thresholds.*` |
| Enrichment | `enrichment.enabled`, database/table/join/timestamp fields, coverage floor, and specification-view controls |

EDA exit codes are `0` success, `1` audit failure, `2` usage, `3` schema error,
`4` run-selection error, and `5` critical integrity finding.

`latest-valid` means the newest run with valid status, a finish time, applied
comparison, and a non-empty discovery. It does not exclude manual or synthetic
triggers. Explicit numeric selection accepts an invalid run with a warning.

`max_rows_in_memory` and `chunk_size` are accepted configuration fields but are not
currently wired into dataset construction; changing them does not enforce a memory
bound or alter query batching in this revision.
`include_left_truncated`/`--include-left-truncated` is recorded in provenance but
does not currently change duration-rank exclusions. `log_level` is accepted but no
EDA logging setup consumes it. `cache_dir` is created but not otherwise used, and
`enrichment.spec_view` is ignored because the implementation pivots the long
`trim_specs` table directly.

## Configuration precedence

- Base, deep, and monitor: explicit CLI value, then YAML value, then model default.
  Their `--config` option precedes the command.
- EDA: explicit subcommand option, then YAML value, then model default. Its database
  model default resolves environment variables in the order below.
- Unknown YAML keys are rejected by all four Pydantic models.
- A path passed to `--config` that does not exist currently falls back to defaults;
  verify paths explicitly rather than relying on that behavior.
- Relative filesystem paths and relative SQLite URLs are interpreted from the
  process working directory, not from the directory containing the YAML file.

Not every configuration model field has a matching CLI flag. The example YAML files
are the reference for config-only values. Conversely, a model accepting a field does
not prove the implementation consumes it; current inert fields are listed under the
deep command boundary above and in
[Project status and known limitations](project-status-and-limitations.md#accepted-but-incomplete-options).

For the exhaustive current field/default/CLI matrix, see
[Configuration defaults](configuration-defaults.md).

## Exit status summary

| Surface | Status | Meaning |
|---|---:|---|
| Base | `0` | Command completed or audit passed |
| Base | `1` | Validation/audit checks failed |
| Base | `130` | Interrupted; checkpoint cleanup ran |
| Deep | `0` | Command completed or audit passed |
| Deep | `1` | Blocked phase or failed audit |
| Deep | `2` | Usage, seed, or unknown-command error |
| Deep | `130` | Interrupted; progress retained |
| Monitor | `0` | Command-specific success |
| Monitor | `1` | Invalid/failed daily run, failed validation/snapshot, or rejected worksheet rows |
| Monitor | `2` | Missing run/usage/unknown-command case handled by the CLI |
| EDA | `0` | Success |
| EDA | `1` | Audit failed or generated output contained a forbidden conclusion phrase |
| EDA | `2` | Usage or unknown command |
| EDA | `3` | Schema is not analysable |
| EDA | `4` | Run selection failed |
| EDA | `5` | Critical integrity finding |

`argparse` can return its own usage status before application code runs. Unhandled
configuration, filesystem, dependency, database, or network exceptions can also
terminate outside the application-specific table; automation must capture stderr and
the recorded run state, not rely on an exit number alone.

## Environment variables and secrets

| Variable | Consumer | Purpose |
|---|---|---|
| `BAMA_MONITOR_DATABASE_URL` | Monitor, EDA fallback, backup tools | SQLite or PostgreSQL database URL |
| `BAMA_EDA_DATABASE_URL` | EDA | First-choice EDA database URL |
| `BAMA_DATABASE_URL` | EDA | Second-choice generic database URL |
| `BAMA_MONITOR_WEBHOOK_URL` | Monitor alerts | Webhook sink |
| `BAMA_MONITOR_SMTP_URL` | Monitor alerts | SMTP connection URL |
| `BAMA_MONITOR_ALERT_EMAIL` | Monitor alerts | Alert recipient |
| `POSTGRES_PASSWORD` | Docker Compose | PostgreSQL container password |
| `BAMA_BACKUP_DIR` | Backup script | Dump destination |
| `BAMA_RETAIN_DAILY` | Backup script | Daily retention count |
| `BAMA_RETAIN_WEEKLY` | Backup script | Accepted and reported, but the current pruning logic does not implement a working weekly tier |
| `BAMA_ENV_FILE` | macOS installer | Alternate monitor environment-file path |

For EDA, the database fallback order is `BAMA_EDA_DATABASE_URL`,
`BAMA_DATABASE_URL`, `BAMA_MONITOR_DATABASE_URL`, then the default local SQLite file.
Keep secret files outside version control and restrict their permissions.

The current macOS wrapper prints `BAMA_MONITOR_DATABASE_URL` to its stdout log. Do
not put credentials in that URL until the wrapper redacts it. See
[Project status and known limitations](project-status-and-limitations.md#macos-launchd).
