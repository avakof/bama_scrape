# Operate the daily monitor

The monitor records one saved search at a fixed local time, compares only valid runs,
and preserves every state transition and inference as auditable evidence.

## Install and configure

For local SQLite operation:

```bash
pip install -e .
pip install apscheduler tzdata
python -m bama_monitor migrate
python -m bama_monitor validate
```

These defaults use repository-local SQLite and output paths. For an explicit local
file, copy `config.monitor.example.yaml` to `config.monitor.yaml`. The deployment
example's `/data` values are container-oriented and are not valid defaults for the
native systemd unit.

For PostgreSQL, also install `psycopg[binary]` and set a secret environment value:

```bash
export BAMA_MONITOR_DATABASE_URL="postgresql://USER:PASSWORD@HOST/DB"
```

Do not put production credentials in the YAML file.

## Run manually

```bash
python -m bama_monitor --config config.monitor.yaml run-daily
```

The default trigger type is `manual`, and a manual run has no production slot. This
means testing by hand cannot satisfy or block the scheduled run.

Use `--scheduled-for` only with a genuine production trigger managed by the scheduler:

```bash
python -m bama_monitor --config config.monitor.yaml run-daily \
  --trigger-type catch_up \
  --scheduled-for "2026-08-10T11:00:00+00:00"
```

Do not fabricate historical observations. A catch-up records both the intended slot
and actual start; a missed slot beyond the grace window is recorded as missed.

The scheduler daemon is the implementation that reconciles slots, distinguishes
scheduled from catch-up execution, publishes validated snapshots, and advances the
genuine-history markers. A plain `run-daily` invocation does none of that implicitly;
its default remains manual.

## Inspect and maintain a run

```bash
python -m bama_monitor --config config.monitor.yaml status
python -m bama_monitor --config config.monitor.yaml history
python -m bama_monitor --config config.monitor.yaml report --run-id 123
python -m bama_monitor --config config.monitor.yaml compare --run-id 123
python -m bama_monitor --config config.monitor.yaml verify-missing --run-id 123
python -m bama_monitor --config config.monitor.yaml refresh-details
python -m bama_monitor --config config.monitor.yaml detect-reposts
```

`compare` reports the stored comparison and does not reapply transitions. Separate
maintenance commands are useful for diagnosis, but `run-daily` remains the canonical
orchestrated pipeline.

## Publish a snapshot

```bash
python -m bama_monitor --config config.monitor.yaml snapshot --run-id 123 --validate-only
python -m bama_monitor --config config.monitor.yaml snapshot --run-id 123
```

Validation checks run health, completion, comparison persistence, detail accounting,
and temporal consistency. Invalid runs do not advance `latest_valid.json`.
`--allow-invalid` exports only the run-specific directory and is for forensic use.

## Analytics and calibration

```bash
python -m bama_monitor --config config.monitor.yaml analytics --view daily_inventory
python -m bama_monitor --config config.monitor.yaml analytics \
  --view market --dimension brand
python -m bama_monitor --config config.monitor.yaml validation-sample \
  --per-band 25 --out reports/validation_worksheet.csv
python -m bama_monitor --config config.monitor.yaml validation-ingest \
  --worksheet reports/validation_worksheet.csv --labelled-by manual
python -m bama_monitor --config config.monitor.yaml calibration
```

Calibration reports measured outcomes only after explicit human labels. Until the
minimum label threshold is met, sale scores remain uncalibrated evidence rankings.

## Slot listing and simulation

```bash
python -m bama_monitor --config config.monitor.yaml backfill \
  --from-date 2026-08-01 --to-date 2026-08-07
```

Despite its historical command name, `backfill` does **not** create runs or recover
past inventory. It only prints the scheduled slot instants in the requested UTC date
range. The current site cannot reconstruct what a saved search contained on a past
day.

Use `tools/simulate_days.py` for offline state-machine scenarios. Synthetic scenarios
must never be presented as market observations or included in genuine production
history.

## Health and alerts

If a run is invalid, inspect its health checks and scrape errors before retrying. The
inventory evidence is retained, but no listing states are advanced. Supported alert
sinks use environment variables documented in
[CLI and configuration](../reference/cli-and-configuration.md#environment-variables-and-secrets).
Alert conditions are persisted before dispatch filtering, so disabling sinks or
raising `alerts.min_severity` does not remove their database record.

The configuration contains a detail concurrency setting, but the current daily
census loop awaits listing scrapes serially. Do not use that setting to estimate
parallel throughput.

`dry_run`/`--dry-run` is accepted but is not currently consulted by the monitor
pipeline. It does not make a network command safe to execute offline.

Several other accepted tuning fields are not wired in this revision:
`persistent_absence_days`, `repost.price_bucket_toman`, and
`detail.active_refresh_days_min`. See the complete configuration reference before
tuning policy.

For unattended scheduling, backup, and recovery, continue with
[Deploy and recover](deploy-and-recover.md). The packaged systemd and Docker paths
have unresolved blockers; review that guide before enabling a timer.
