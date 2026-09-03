# Getting started

This guide creates a clean development installation, verifies all four command
surfaces without contacting Bama, and then points to the appropriate first workflow.

## Requirements

- Python 3.11 or newer.
- SQLite, included with Python, for local collection and monitoring.
- A Chromium download from Playwright only for `inspect`, scroll-mode discovery,
  and browser fallback.
- PostgreSQL client/server tools only when operating the monitor with PostgreSQL or
  running backup drills.
- Linux with systemd, macOS with launchd, or Docker Compose only for the matching
  deployment guide.

## Create the environment

```bash
git clone https://github.com/avakof/bama_scrape.git
cd bama_scrape
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

On Windows, activate with `.venv\Scripts\activate`.

Install additional profiles explicitly:

```bash
# Browser-backed inspection or discovery
playwright install chromium

# Development and verification
pip install -e ".[dev]"

# EDA charts and statistics
pip install -e ".[eda]"

# Monitor scheduler and PostgreSQL adapter
pip install "psycopg[binary]" apscheduler tzdata
```

The monitor-specific packages are intentionally documented separately because the
current package metadata does not expose a `monitor` extra.

## Verify the command surfaces offline

These commands parse help and exit without opening a database or making a request:

```bash
python -m bama_scraper --help
python deep_scraper/run_deep.py --help
python -m bama_monitor --help
python -m bama_eda --help
```

For contributors, run the offline suite:

```bash
pytest -q
mypy src deep_scraper/bama_deep
ruff check src tests tests_monitor deep_scraper tools
ruff format --check src tests tests_monitor deep_scraper tools
```

No test contacts the live website. Network behavior is exercised with sanitized
fixtures and local synthetic pages.

The tracked `notebooks/bama_eda_overview.ipynb` currently has concatenated code-cell
source and fails repository-wide Ruff checks. It is not part of the passing command
set above. See the project-status page before using or repairing it.

## Choose a database

The base and deep scrapers use SQLite. The monitor and EDA support both SQLite and
PostgreSQL.

| Use | Recommended database | Why |
|---|---|---|
| First run or local development | SQLite | No service or credentials required |
| One-time scrape | SQLite | Native source of truth for resumable collection |
| Unattended production monitoring | PostgreSQL | Operational tooling, concurrent access, and backup workflow |
| EDA | Same monitor database through the query-only EDA API | Avoids stale exported copies; use database permissions for enforced read-only access |

SQLite URLs use three slashes before a relative path:
`sqlite:///monitor_data/bama_monitor.sqlite`. PostgreSQL credentials should be
provided through `BAMA_MONITOR_DATABASE_URL`, not committed to YAML.

## Copy configuration

Choose only the example relevant to the workflow:

```bash
cp config.example.yaml config.yaml                    # base scraper
cp deep_scraper/config.example.yaml deep_scraper/config.yaml
cp config.monitor.example.yaml config.monitor.yaml    # monitor
cp config.eda.example.yaml config.eda.yaml             # EDA
```

The monitor example uses repository-local SQLite, output, snapshot, report, and lock
paths. `deploy/config.example.yaml` is a partial, container-oriented override and
uses `/data`; do not substitute it for the local example or copy it into a native
service without reconciling the service's writable paths.

The repository's current `.gitignore` ignores files named `config.yaml`, but not
`config.monitor.yaml` or `config.eda.yaml`. Those two local copies can therefore
appear in `git status`; never stage them if they contain paths, credentials, or
operator-specific policy.

For the base scraper, deep scraper, and monitor, `--config` is a top-level option
and must come before the subcommand. EDA accepts `--config` on each subcommand.
Explicit CLI values override YAML values; omitted values fall back to the model
defaults. Not every model field has a CLI flag; config-only fields belong in YAML.
Database environment-variable resolution is documented in
[CLI and configuration](reference/cli-and-configuration.md#environment-variables-and-secrets).
The exhaustive field/default inventory is in
[Configuration defaults](reference/configuration-defaults.md).

## First verified workflow

The least expensive parser/storage smoke path limits detail work, but `discover`
still walks the entire configured search. Choose a genuinely narrow public search
URL if you need a small network run; `--limit` does not limit discovery. Confirm the
target URL and responsible-use rules first.

```bash
python -m bama_scraper --config config.yaml discover
python -m bama_scraper --config config.yaml scrape-details --limit 5
python -m bama_scraper --config config.yaml export
```

At this point an offline validation is expected to fail if more than five detail
records remain pending; that failure correctly says the dataset is incomplete. To
finish and then require a passing audit:

```bash
python -m bama_scraper --config config.yaml scrape-details --resume
python -m bama_scraper --config config.yaml validate --skip-second-pass
```

Continue with one of these guides:

- [Collect a dataset](how-to/collect.md)
- [Run deep enrichment](how-to/deep-enrichment.md)
- [Operate the monitor](how-to/monitor.md)
- [Run EDA](how-to/analyze.md)

## What success looks like

- Every command surface prints help without an import error.
- The selected example configuration loads without unknown keys.
- A base-scraper smoke run creates `output/bama_ads.sqlite` and records all errors
  instead of silently dropping rows.
- An offline validation pass explains its result in `run_summary.json`.

These checks do not validate live-site compatibility, systemd/Docker deployment, or
backup retention. Review
[Project status and known limitations](reference/project-status-and-limitations.md)
before unattended operation.

If any of those fail, use [Troubleshooting](reference/troubleshooting.md) before
increasing concurrency or retrying repeatedly.
