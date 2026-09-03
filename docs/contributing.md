# Contributing

The repository values reproducible evidence, conservative network behavior, and
explicit uncertainty. A change is incomplete if it makes output easier to produce but
harder to audit.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,eda]"
playwright install chromium
```

Scheduler/PostgreSQL work also needs:

```bash
pip install "psycopg[binary]" apscheduler tzdata
```

## Repository layout

| Area | Responsibility |
|---|---|
| `src/bama_scraper/` | One-time collection, normalization, storage, validation, export |
| `deep_scraper/bama_deep/` | Deep endpoints, parsers, merge, queue, audit, export |
| `src/bama_monitor/` | Daily state, health, persistence, scheduling, reports |
| `src/bama_eda/` | Query-only dataset building, validation, statistics, charts |
| `migrations/` | Portable and dialect-specific monitoring schema changes |
| `deploy/` | systemd, Docker, backup, and macOS launchd assets |
| `tests/`, `deep_scraper/tests/`, `tests_monitor/` | Offline behavior and integration tests |
| `docs/` | Canonical user, operator, research, and contributor documentation |

The monitor reuses base discovery and deep parsing code. Do not copy a parser into the
monitor to make a local change easier.

## Quality checks

```bash
pytest -q
mypy src deep_scraper/bama_deep
ruff check src tests tests_monitor deep_scraper tools
ruff format --check src tests tests_monitor deep_scraper tools
```

The narrower Ruff commands above describe the current passing source boundary. The
tracked EDA notebook has malformed concatenated code cells and fails when included;
fix and review it separately before restoring `notebooks` to the passing command.

Tests must not contact Bama. Use local synthetic servers for browser behavior and
sanitized or synthetic fixtures for payload parsing. Network access belongs in an
explicit operator run, never an implicit unit test.

## Test expectations

- Parsing changes include minimal fixtures for success, absence, malformed input,
  and privacy scrubbing.
- Discovery changes prove bounded termination, deduplication, and resume behavior.
- Monitor changes prove invalid-run gating, idempotency, and state transitions across
  more than one day.
- Migration changes exercise both SQLite and PostgreSQL spellings where applicable.
- Analysis changes prove data grain, temporal cutoff, missingness, small-sample, and
  censoring behavior.
- Documentation changes explicitly verify local links/anchors, command inventory,
  example configuration loading, and notebook syntax. No tracked contract test
  currently automates all four checks.
- Deployment changes prove path permissions, timer syntax, trigger provenance,
  snapshot durability, secret redaction, and restore behavior on the target platform.

Avoid assertions tied to a current live inventory count. Live evidence belongs in a
dated manual record under `docs/evidence/`.

## Privacy and fixtures

Before adding a captured fixture, remove phone numbers, names, exact addresses, and
unrelated seller text. Keep only the smallest structure needed for the test. Run the
privacy audit and review `git diff` because `.gitignore` cannot sanitize a tracked
file.

Never commit runtime databases, exports, logs, reports, screenshots, failed HTML,
local YAML, or environment files.

## Documentation maintenance

- Keep the root README short; canonical detail belongs under `docs/`.
- Document workflows, not mutable output counts.
- Put dated measurements and deployment experiments under `docs/evidence/`.
- Update and manually compare the command table whenever an argparse subcommand
  changes; add an automated contract when the repository gains one.
- Update the relevant example YAML and configuration reference together.
- Preserve compatibility landing pages when moving content.
- State grain, units, provenance, and unsupported conclusions near analytical output.
- Keep notebook logic thin and backed by package functions.
- Update [Project status and known limitations](reference/project-status-and-limitations.md)
  only when code and verification evidence justify closing or adding a boundary.

## Database changes

Monitoring migrations are append-only and applied in filename order. Use portable
tokens for shared SQL and a `.sqlite.sql`/`.postgres.sql` pair only when syntax cannot
be rendered portably. Never modify an already applied migration to change production
history.

SQLite and PostgreSQL behavior should remain semantically equivalent even when their
DDL differs.

## Before handing off

Report which checks ran, whether network access was used, and any working-tree files
you intentionally did not modify. Do not claim live behavior from unit tests or claim
a deployment succeeded until the scheduler and restore path have been observed.

Do not commit an unrelated dirty-tree file merely because it makes a documentation
example look current. Either document the committed implementation, or include the
implementation and its tests as an explicitly reviewed change.
