# Project status and known limitations

This page is the conservative status statement for the revision in this repository.
It separates behavior verified by offline tests from behavior that still depends on
a live website, a particular host, or unfinished deployment wiring.

Status reviewed: **2026-09-03**.

## Verification boundary

In an isolated checkout of the staged revision, all 1,057 tracked offline tests,
four command-help surfaces, source lint/format checks, and mypy pass under local
Python 3.14.4. Those checks exercise parsers, termination, storage, migrations,
monitoring transitions, reports, and analysis with fixtures and synthetic databases.
They do not prove that Bama's current HTML/API shapes, access policy, inventory, or
rate limits are unchanged.

Repository-wide Ruff checks that include `notebooks/bama_eda_overview.ipynb` do not
pass in the committed revision. The notebook's JSON source strings omit line breaks,
so Ruff reads each code cell as concatenated statements. Repair and review the
notebook as its own focused change before treating it as executable documentation.

The latest retained live-site and automation observations are dated records under
[`docs/evidence`](../evidence/README.md). Treat them as historical evidence, not a
service-level promise.

## Capability status

| Area | Current status | Important boundary |
|---|---|---|
| Base collection | Offline-tested; previously exercised live | Live endpoint and Nuxt assumptions can change; re-run `inspect` before a consequential collection |
| Deep enrichment | Offline-tested; previously exercised live | Some accepted configuration fields are not implemented; use `plan`, not `dry_run`, as the network-free budget command |
| Monitoring core | Offline-tested on synthetic histories | A disappearance is observed; a sale remains an uncalibrated inference |
| EDA | Package is offline-tested against synthetic monitoring databases | The tracked notebook is malformed; the API issues queries only, but the database connection is not transport-enforced read-only |
| macOS launchd assets | Previously exercised on one host | The wrapper currently logs the configured database URL and the installer preflight does not load the environment file |
| Native systemd assets | **Not production-ready as shipped** | Path, timer, trigger-provenance, and snapshot-publication mismatches remain |
| Docker/systemd assets | **Not production-ready as shipped** | Timer-triggered runs are currently manual runs and snapshots are not durably wired |
| PostgreSQL backup script | Dump/verify/restore/drill paths exist | The advertised weekly-retention tier is not implemented by the current filename/pruning logic |

## Unattended-deployment blockers

Do not describe the packaged systemd or Docker wiring as a production deployment
until the following items are fixed and exercised on the target host.

### Native systemd

- The installer migration command and service invocation put the top-level
  `--config` option after the monitor subcommand. `argparse` rejects that order, so
  the packaged native path cannot complete successfully as committed.
- `deploy/install_systemd.sh` copies the deployment example with `/data/...` paths,
  while the native service creates and permits writes only below
  `/opt/bama-monitor/monitor_data` and `/opt/bama-monitor/reports`. The default
  install can therefore fail before or during migration.
- `MonitorConfig.daily_snapshot_dir` defaults outside the service's
  `ReadWritePaths`, and the deployment YAML does not override it.
- The timer uses a `Timezone=` key in `[Timer]`, but the documented systemd timer
  interface expresses a zone inside the `OnCalendar=` calendar expression. The
  installer likewise passes an unsupported `--timezone` option to
  `systemd-analyze calendar`. See the upstream
  [`systemd.timer`](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)
  and
  [`systemd.time`](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html)
  manuals.
- The service invokes plain `run-daily`. That command defaults to
  `--trigger-type manual`, has no production slot, and exports no immutable snapshot
  unless `--export-snapshot` is supplied. Consequently a timer firing is not
  currently recorded as genuine scheduled history.

### Docker plus host systemd

- The Dockerfile default command and healthcheck, Compose migration command, and
  Docker host service all use the same invalid post-subcommand `--config` ordering.
  Their normal paths fail parsing before the monitor workflow runs.
- The Docker service has the same manual-trigger and no-snapshot behavior as the
  native service.
- Without an explicit `/data/daily_snapshots` configuration, the default snapshot
  directory is inside the disposable container rather than the persistent `/data`
  volume.
- The container service mounts the named `monitordata` volume and also bind-mounts
  `../reports`; operators must verify the resulting ownership and paths on the
  actual host.

### macOS launchd

- The rendered scheduler wrapper prints `BAMA_MONITOR_DATABASE_URL` to
  `logs/scheduler.out.log`. A PostgreSQL URL can contain a clear-text username and
  password. Use SQLite without credentials or patch the wrapper to redact the URL
  before supplying a credential-bearing URL.
- The installer's database preflight does not source the configured environment
  file, so it can validate the default SQLite database even when the launched
  process will use PostgreSQL. Validate the configured database separately.
- After installation, operate the generated `*.rendered.sh` scripts. The source
  templates still contain `@...@` placeholders and are not runnable directly.
- A launch agent cannot collect while the Mac is powered off. A sleeping-laptop
  recovery path has tests, but the retained evidence does not demonstrate a long
  reliability record.

## Backup limitation

`deploy/backup.sh` creates PostgreSQL custom-format dumps, lists their contents,
restores only into a new database, and can compare core table counts in a scratch
restore drill. Its `BAMA_RETAIN_WEEKLY` value is currently reported but not used to
select or retain weekly files: dump names contain no weekly marker matched by the
pruning expression. Treat retention as the daily age window only, or implement and
test a real weekly tier before relying on it.

The verification command also writes a fixed temporary path under `/tmp`; concurrent
verification processes can overwrite that listing. Run backup maintenance
serially.

## Collection and parser limitations

- The target is one filtered search, not the whole vehicle market.
- Search inventory changes during a pass. A count is meaningful only with the run
  ID, timestamps, URL, and configuration.
- API counters observed in the dated evidence were not stable enough to be a
  termination authority. The scraper therefore depends on repeated observed
  emptiness; a changed endpoint can invalidate that assumption.
- Detail parsing depends primarily on the Nuxt devalue payload, with JSON-LD/DOM
  fallbacks. A frontend change may reduce fields or produce partial records.
- A 404/410 after discovery records a listing that became unavailable; it does not
  prove a sale.
- Jalali/Gregorian year normalization is useful at year grain, not an exact date
  conversion.
- Image-byte download in the base scraper does not deduplicate identical images
  across advertisements.

## Privacy and data handling limitations

The privacy boundary differs by component:

- The **base scraper** recursively removes known contact-field keys, but it does
  not redact contact details that a seller typed into free-form description text.
  Its audit checks for contact keys, not arbitrary phone-number patterns. Treat the
  base database, JSONL/CSV/Parquet exports, and saved HTML as potentially containing
  personal data.
- The **deep scraper** removes a broader contact-key set and redacts supported phone,
  email, and messaging-handle patterns from selected free-text fields before
  persistence. Pattern-based redaction is defensive, not a mathematical guarantee
  against every format.
- The **monitor** uses the deep advertisement parsers for detail data. Its snapshots
  can still contain public seller/dealer names, locations, descriptions, and image
  URLs.
- **EDA** defensively scrubs contact-shaped text again before outputs, but analytical
  exports can still contain public listing identifiers and quasi-identifiers.

Never publish raw databases, descriptions, saved HTML, screenshots, logs, or image
collections without a separate privacy and rights review.

## Accepted but incomplete options

- Deep `raw_description` is accepted by `DeepConfig` but is not consumed by the
  export implementation.
- Deep `json_logs` is accepted by `DeepConfig`, but the deep CLI passes only
  `log_level` into the logging setup.
- Deep `download_images` is accepted by configuration and CLI parsing but no deep
  phase downloads image bytes.
- Deep `dry_run` short-circuits the standalone `ads` command only. It does not guard
  `run`, `specs`, `prices`, or `dealers`. Use the `plan` command when no network
  request is allowed.
- Deep `run --limit N` limits phase A advertisement work; it does not cap every
  enrichment phase to `N` entities.
- Deep `max_requests` and `requests_issued` count successful responses. Transport
  retries and failed requests are not counted, so the setting is not a hard ceiling
  on actual HTTP attempts.
- Monitor `dry_run`/`--dry-run` is accepted but is not consumed by the command
  pipeline. It does not prevent network or database work.
- Monitor `persistent_absence_days` is accepted and included in the configuration
  hash, but sale scoring currently uses a literal seven-day test. Repost
  `price_bucket_toman` and detail `active_refresh_days_min` are accepted but not
  read; the refresh path uses `active_refresh_days_max` only.
- The monitor detail policy has a `concurrency` setting, but the daily census loop
  currently awaits advertisement scrapes serially. The setting still configures the
  shared fetcher but should not be interpreted as observed parallel throughput.
- Repost image evidence is a SHA-1 digest of normalized image URL paths, not a
  perceptual hash of downloaded image content.
- EDA `max_rows_in_memory` and `chunk_size` are accepted model fields but are not
  currently passed into dataset construction. They do not enforce those bounds in
  this revision.
- EDA `include_left_truncated` is recorded in provenance but does not currently
  alter the unconditional eligibility/exclusion path. `log_level` has no logging
  consumer, `cache_dir` is only created, and `enrichment.spec_view` is ignored in
  favor of pivoting the long `trim_specs` table.

## Analysis limitations

- `latest-valid` selects the newest valid, finished, comparison-applied, non-empty
  run. It does **not** exclude manual, deployment-test, backfill, or synthetic runs.
  For production-only analysis, inspect `history` and pass an explicit run ID.
- Explicitly selecting an invalid run produces a warning rather than refusing the
  run; absence signals from such a run are not trustworthy.
- The `ReadOnlyDatabase` API exposes read methods and disables migrations, but its
  SQLite connection can create a missing file/directory and set WAL-related pragmas.
  Use filesystem/database permissions when enforced read-only access is required.
- Asking prices are not transaction prices. Disappearance timing is interval-censored,
  older listings are left-truncated, and active listings are right-censored.
- Sale-evidence scores are editorial weighted ranks until calibrated against enough
  independently labelled outcomes; they are not probabilities.
- Deep enrichment is a second data source with its own timestamps. It is opt-in and
  must satisfy the analysis cutoff to avoid future leakage.

## Packaging and governance

- The package metadata installs only the base dependencies. Scheduler/PostgreSQL
  operation also requires `apscheduler`, `tzdata`, and `psycopg[binary]`.
- Only the base scraper has a console-script entry point. Deep, monitor, and EDA use
  the module/script commands documented in the CLI reference.
- There is currently no CI workflow in the repository.
- There is no tracked documentation-contract test in this revision. Local links,
  anchors, command help, and all five example configurations were checked manually
  for this commit, but future changes need an automated equivalent.
- `.gitignore` does not currently match the documented local filenames
  `config.monitor.yaml` or `config.eda.yaml`; review staging carefully and never put
  database credentials in either file.
- There is no `LICENSE`, `COPYING`, or equivalent grant. Public visibility on GitHub
  does not itself grant permission to copy, modify, or redistribute the code or data.

When a limitation is fixed, update the implementation, its tests, the relevant
how-to guide, and this status page in the same commit. Do not remove a warning based
only on static review; record the deployment or live verification that closed it.
