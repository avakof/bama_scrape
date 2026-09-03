# Deploy, back up, and recover

The monitoring database is an irreplaceable observation history: a missed past
inventory cannot be reconstructed from today's website. Deployment must therefore
prove schedule provenance, durable paths, snapshot publication, secret handling, and
restore—not merely that a process started.

> [!CAUTION]
> The packaged native systemd and Docker/systemd paths are **not production-ready as
> shipped**. They contain verified configuration and provenance mismatches described
> below. Do not enable their timers until those items are fixed, tested, and recorded
> for the target host.

## Deployment acceptance criteria

Before calling any installation production-ready, verify all of the following:

1. Exactly one scheduler owns the saved search and schedule name.
2. The configured database, report, lock, and daily-snapshot paths are writable and
   durable across process/container restarts.
3. A timer-created run records `trigger_type=scheduled` or an honest `catch_up`, a
   real `scheduled_for` slot, `is_synthetic=false`, and the production schedule name.
4. A healthy run publishes an immutable snapshot and updates `latest_valid.json`;
   an invalid run cannot update it.
5. A manual smoke run has no production slot and cannot satisfy the day's schedule.
6. The process does not print database passwords, webhook URLs, SMTP credentials, or
   alert recipients.
7. A missed slot is recorded rather than backdated or silently ignored.
8. A fresh dump can be restored into a new database and core row counts match.
9. Restart/replay does not create a second genuine run for the same schedule slot.
10. Current Bama access behavior has been checked separately from offline tests.

## macOS launchd path

The macOS path runs `bama_monitor.scheduler_daemon` under launchd. The daemon owns
timezone-aware slot reconciliation, scheduled/catch-up provenance, missed-slot
recording, snapshot publication, and history-boundary updates.

Create the editable environment first and use SQLite unless the credential logging
issue below has been fixed:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install apscheduler tzdata
./deploy/macos/install_launchd.sh
```

The installer renders machine-specific scripts. Operate those rendered files, not
the source templates containing `@...@` placeholders:

```bash
./deploy/macos/status_launchd.rendered.sh
./deploy/macos/run_once.rendered.sh
./deploy/macos/uninstall_launchd.rendered.sh
```

The uninstall command removes the launch agent and generated scripts; it preserves
the database, reports, snapshots, logs, source, and environment file.

Current macOS limitations:

- `scheduler_wrapper.rendered.sh` prints `BAMA_MONITOR_DATABASE_URL` into
  `logs/scheduler.out.log`. Do not supply a password-bearing PostgreSQL URL until
  that output is redacted.
- The installer's database preflight does not source the selected environment file.
  It can validate default SQLite while the launched process is configured for a
  different database. Run a second validation with the environment loaded.
- A powered-off Mac cannot observe the market. A sleeping Mac depends on watchdog
  reconciliation and the grace window; retained evidence covers only one host and
  is not a reliability guarantee.

After install, verify the active process, next trigger, environment-independent
timezone calculation, and actual database:

```bash
./deploy/macos/status_launchd.rendered.sh
.venv/bin/python -m bama_monitor.scheduler_daemon --print-next
set -a
source deploy/macos/monitor.env
set +a
.venv/bin/python -m bama_monitor validate
```

The source `deploy/macos/README_MACOS.md` contains platform-specific launchctl and
log commands. Dated observations are in
[automation evidence](../evidence/automation-evidence.md).

## Native Linux/systemd path

The intended design is a oneshot service triggered daily by a persistent timer, with
PostgreSQL backup units. The current assets need correction before use:

- The installer migration command and native service place the top-level `--config`
  option after the subcommand. The monitor parser requires it before the subcommand,
  so the packaged install/run path fails argument parsing as committed.
- `deploy/config.example.yaml` uses `/data/...`, but the installer creates and the
  service permits writes under `/opt/bama-monitor/...`.
- `daily_snapshot_dir` is not set to an allowed native path.
- The timer's `Timezone=` directive and the installer's
  `systemd-analyze calendar --timezone=...` invocation do not match the upstream
  timer/calendar interfaces. Put the IANA zone in the `OnCalendar=` expression and
  validate that exact expression on the target systemd version.
- The service runs plain `run-daily`, whose default trigger is `manual`, and does not
  request snapshot export. A timer firing therefore lacks genuine production
  provenance and the documented immutable snapshot.
- Calling `run-daily --trigger-type scheduled --export-snapshot` alone is not yet
  equivalent to the daemon's post-run history-boundary logic. The complete slot and
  publication contract needs an integration test before choosing the final unit
  command.

The authoritative systemd syntax is documented by the upstream
[`systemd.timer`](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)
and
[`systemd.time`](https://www.freedesktop.org/software/systemd/man/latest/systemd.time.html)
manuals.

Once the assets are fixed, validate without immediately enabling a timer:

```bash
systemd-analyze verify \
  deploy/bama-monitor.service \
  deploy/bama-monitor.timer \
  deploy/bama-monitor-backup.service \
  deploy/bama-monitor-backup.timer
systemd-analyze calendar --iterations=5 '*-*-* 13:00:00 Europe/Paris'
```

Then perform a manual, non-slot-owning smoke run, inspect its row, and exercise the
real timer path in a controlled window. Only after the resulting run and snapshot
meet the acceptance criteria should the timer be enabled.

## Docker Compose plus host systemd

The Compose stack provides PostgreSQL, a migration task, and a one-run monitor task;
the host timer is intended to schedule it. The image default command, healthcheck,
Compose migration command, and host service currently put `--config` after the
subcommand, so their normal invocations fail argument parsing. After that is fixed,
the stack still shares the systemd provenance problem: the unit invokes plain
`run-daily`, so the run is manual and no snapshot is requested.

There is also a persistence mismatch. The monitor's default `daily_snapshot_dir` is
under `/app`, while only `/data` is a durable named volume. Configure snapshots under
`/data/daily_snapshots` and verify the volume contents before relying on them.

The following commands are useful for development after reviewing those limitations;
they are not evidence of a production-ready schedule:

```bash
export POSTGRES_PASSWORD="replace-with-a-secret"
docker compose -f deploy/docker-compose.yml config
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm migrate \
  --config /etc/bama-monitor/config.yaml migrate
docker compose -f deploy/docker-compose.yml run --rm monitor \
  --config /etc/bama-monitor/config.yaml status
```

Avoid putting a real password on a shared shell command line or into version control.
Prefer a protected environment file or secret manager supported by the deployment
environment.

## PostgreSQL backup commands

The backup script supports four operations:

```bash
deploy/backup.sh dump
deploy/backup.sh verify /path/to/bama-monitor-TIMESTAMP.dump
deploy/backup.sh restore /path/to/bama-monitor-TIMESTAMP.dump NEW_DATABASE_NAME
deploy/backup.sh drill
```

- `dump` writes a compressed PostgreSQL custom-format dump.
- `verify` confirms the archive can be listed and contains the four named core
  tables. It does not restore data.
- `restore` calls `createdb` first and therefore refuses an existing target under
  normal PostgreSQL behavior.
- `drill` dumps, verifies, restores to a process-specific scratch database, compares
  core table counts, and drops the scratch database.

The current script does **not** implement the advertised weekly retention tier.
`BAMA_RETAIN_WEEKLY` is printed but dump names never contain the weekly marker used
by the pruning expression. Until fixed, rely only on the daily age window controlled
by `BAMA_RETAIN_DAILY` and copy verified long-term backups using separate tooling.
Run `verify` serially because it writes a fixed listing file in `/tmp`.

## Recovery procedure

1. Stop the single active scheduler so no new write starts during diagnosis.
2. Preserve the current database, failed-run rows, logs, and configuration.
3. Identify a dump by timestamp and run `verify`.
4. Restore into a **new** database name.
5. Point an isolated validation command at the restored database.
6. Compare migration history and counts for `monitoring_runs`, `advertisements`,
   `daily_ad_observations`, and `advertisement_events`.
7. Inspect the latest valid run, genuine-history boundary, missed slots, open alerts,
   and snapshot pointer.
8. Change the production environment only after the restored copy passes review.
9. Start the one chosen scheduler and confirm its next slot.
10. After the next real run, verify trigger provenance and snapshot publication.

Example validation against the restored database:

```bash
BAMA_MONITOR_DATABASE_URL="postgresql://USER:PASSWORD@HOST/NEW_DATABASE_NAME" \
  python -m bama_monitor --config /etc/bama-monitor/config.yaml validate
```

Avoid leaving a password-bearing command in shell history. An environment file with
restricted permissions is preferable for real recovery.

## Stale locks and missed runs

Do not delete a lock merely because its timestamp looks old. First confirm no monitor
or scheduler process is active, then inspect both filesystem and database lock state.
Clear only a verified orphan.

A past market observation cannot be backfilled. If the intended slot is still inside
the configured grace window, the scheduler daemon can record a catch-up with both the
true start and intended slot. Outside that window it records a missed slot. The CLI
command named `backfill` only lists slot instants; it does not recreate inventory.

For the consolidated defect list and status boundary, see
[Project status and known limitations](../reference/project-status-and-limitations.md#unattended-deployment-blockers).
