# Troubleshooting

Start with the recorded run summary, error tables, and logs. Do not respond to an
uncertain failure by increasing concurrency or adding evasion behavior.

## Base scraper

| Symptom | Likely cause | Action |
|---|---|---|
| Chromium executable missing | Playwright browser not installed | Run `playwright install chromium` |
| HTTP 400 “PageSize is not Valid” | `api_page_size` exceeds 30 | Restore 30 or lower |
| Discovery finds no ads | Empty filter, endpoint change, or block | Run `inspect`; review network candidates and saved evidence |
| 403, 429, CAPTCHA, or block page | Site is limiting access | Stop, wait, reduce concurrency, and increase delays |
| Detail rows are partial | Nuxt payload changed or fallback parser used | Inspect sanitized failed HTML and parser errors |
| Publication timestamps are null | New relative-date phrase | Preserve raw text and extend the tested normalizer |
| Run appears slow | Conservative delay across a large live inventory | Follow structured progress logs; do not assume a deadlock |
| Persian text is broken in a spreadsheet | Wrong encoding/import path | Open the UTF-8-BOM CSV or import as UTF-8 |
| Base audit passes but a description contains a phone number | Base audit checks known contact keys, not arbitrary free text | Do not publish; run a separate scrub/review or use the deep/EDA privacy pipeline |

Use `validate --skip-second-pass` when you need a purely offline audit.

## Deep scraper

| Symptom | Likely cause | Action |
|---|---|---|
| Seed cannot open source | Missing or wrong base SQLite path | Confirm `source_db` and run base discovery first |
| Request estimate exceeds ceiling | Too many pending entities/phases | Run phases separately or intentionally raise the successful-fetch ceiling; retries are not counted |
| Critical conflict rate fails audit | API and HTML disagree on identity/price/mileage/year/trim | Inspect `ad_field_conflicts`; do not pick a source silently |
| Many records remain `fetching` after interruption | Interrupted checkpoint | Resume normally; queue claiming is recoverable |
| Contact-data audit fails | New payload key or free-text pattern | Stop export, extend scrubber and fixtures, rerun audit |
| Specification view is stale | New keys after enrichment | Run `reindex` before export |
| `--dry-run` still made a request | The flag only short-circuits standalone `ads` | Use `plan` as the network-free command; do not use `run/specs/prices/dealers --dry-run` |
| `--download-images` produced no files | Deep image-byte download is not implemented | Use stored URLs only or implement and test the option before relying on it |

## Monitor

| Symptom | Likely cause | Action |
|---|---|---|
| Run is invalid after a large inventory drop | Truncated discovery, block, or endpoint change | Inspect health findings; do not apply comparison manually |
| Scheduled run did not occur on macOS | Machine slept/off or daemon unavailable | Inspect launchd and daemon logs, `history`, missed slots, and grace window |
| Scheduler reports existing run | Another process holds a lock | Confirm the process; wait or clear only a verified stale lock |
| Manual run seems absent from daily history | Correct provenance behavior | Use `status`/run tables; manual runs do not count as genuine history |
| Migration fails on PostgreSQL | Missing driver/credentials/permissions | Install `psycopg[binary]`; verify URL and schema privileges |
| Snapshot refuses export | Run failed validation or detail coverage | Use `--validate-only`; fix the source run rather than forcing latest-valid |
| Alert not delivered | Sink environment missing or severity filtered | Check environment-file permissions, alert config, and stored alerts |
| `backfill` printed slots but created no runs | This is the implemented behavior | Historical inventory cannot be reconstructed; the command is a slot calculator |

Print the scheduler's interpretation without waiting:

```bash
python -m bama_monitor.scheduler_daemon --config config.monitor.yaml --print-next
```

### Packaged deployment failures

The current native systemd and Docker/systemd assets have known blockers. Diagnose
them against [Project status](project-status-and-limitations.md#unattended-deployment-blockers)
before changing application thresholds:

| Symptom | Cause in the current revision | Required correction |
|---|---|---|
| Service or container reports “unrecognized arguments: `--config`” | Packaged native and Docker commands put the global option after the subcommand | Put `--config FILE` before `run-daily`, `migrate`, or `validate` in every invocation |
| Native install cannot create `/data/...` | Docker-oriented config is copied into a service restricted to `/opt/bama-monitor/...` | Use a native config whose paths match `ReadWritePaths` and add a durable snapshot path |
| Timer unit fails verification or uses host timezone | `Timezone=` is not the documented timer setting | Put `Europe/Paris` in the `OnCalendar=` expression and validate on the host |
| Timer run appears as `manual` | Service invokes plain `run-daily` | Redesign and integration-test the unit command for scheduled provenance |
| No immutable snapshot after timer run | Service does not request publication | Wire and test snapshot publication/history updates |
| Docker snapshot disappears | Default path is inside `/app`, outside the `/data` volume | Configure `/data/daily_snapshots` and verify persistence |
| PostgreSQL password appears in macOS log | Wrapper prints the entire database URL | Redact before using credential-bearing URLs |
| macOS preflight checks the wrong database | Installer does not source the environment file for preflight | Validate again with the configured environment loaded |
| Weekly backups vanish | Weekly retention variable is not implemented by filename/prune logic | Use external retention or implement/test a weekly naming tier |

## EDA

| Symptom | Likely cause | Action |
|---|---|---|
| Schema error | Wrong database or missing migrations | Run monitor migrations and `inspect-schema` |
| No latest valid run | Runs are invalid, unfinished, empty, or comparison not applied | Inspect monitor `history` and choose an explicit valid run |
| Critical integrity exit | Future leakage, broken relationships, or privacy issue | Read the written validation report before rerunning |
| Sparse attributes | Monitor detail snapshots are intentionally selective or young | Opt into a provenanced deep enrichment database |
| No survival chart | Insufficient fully observed subjects/events | Treat the placeholder explanation as the result |
| Persian labels render as boxes | No suitable local font | Install a Persian-capable font or accept technical fallback labels |
| Parquet export fails | Missing/unsupported PyArrow | Install the declared base dependency or disable Parquet output |
| A missing SQLite path suddenly exists | EDA uses a normal SQLite connector beneath its query-only API | Use a read-only copy/role or filesystem permissions; verify the URL first |
| `latest-valid` selected a manual/synthetic run | Run selection does not filter trigger provenance | Inspect monitor `history` and pass an explicit production run ID |
| `--include-left-truncated` changed only the manifest | The option is accepted but not wired into ranking eligibility | Treat left-truncated rows as excluded until the implementation and tests change |

## Documentation checks

If `config.monitor.yaml` or `config.eda.yaml` appears in `git status`, do not assume
it is safe because other local configuration is ignored. The current `.gitignore`
does not match those names; keep credentials in environment variables and leave
operator-local files untracked.

If a repository-wide Ruff check reports notebook cells as one long statement, the
notebook JSON has lost source newlines. That is a known defect in the committed EDA
notebook. Do not suppress the failure; restore valid cell source, review the notebook
logic independently, and add a tracked syntax/contract check with the repair.

This revision has no tracked documentation-contract test. Before committing docs,
manually verify relative links and anchors, compare command tables with each argparse
parser, load every example YAML through its Pydantic model, and compile every Python
notebook cell.
