# Bama longitudinal monitor

`bama_monitor` runs a daily census of one Bama search, applies health-gated state
transitions, checks detail pages, detects possible reposts, computes decomposed sale
evidence, and publishes provenance-rich reports and snapshots.

> A disappearance is an observation. A sale is an inference.

This page remains at its original path for compatibility. Canonical documentation is
split by task:

- [Architecture and data flow](docs/explanation/architecture.md#longitudinal-monitor)
- [Monitoring and analytical methodology](docs/explanation/methodology.md)
- [Operate the monitor](docs/how-to/monitor.md)
- [Deploy, back up, and recover](docs/how-to/deploy-and-recover.md)
- [Monitor CLI and configuration](docs/reference/cli-and-configuration.md#monitor)
- [Complete configuration defaults](docs/reference/configuration-defaults.md#monitor-monitorconfig)
- [Monitoring data and reports](docs/reference/data-and-outputs.md#monitoring-database)
- [Monitor troubleshooting](docs/reference/troubleshooting.md#monitor)
- [Project status and deployment blockers](docs/reference/project-status-and-limitations.md)
- [Dated automation evidence](docs/evidence/automation-evidence.md)

## Quick start

```bash
pip install -e .
pip install apscheduler tzdata              # scheduler support
pip install "psycopg[binary]"              # add for PostgreSQL
python -m bama_monitor migrate
python -m bama_monitor validate
python -m bama_monitor run-daily
```

Copy `config.monitor.example.yaml` to `config.monitor.yaml` when you want every local
setting visible. Put `--config config.monitor.yaml` before the subcommand.

`run-daily` defaults to a manual trigger, which does not occupy a production schedule
slot. The launchd scheduler owns the complete scheduled/catch-up and publication
workflow. The supplied native systemd and Docker/systemd units are not
production-ready in the current revision; do not enable them before reading the
project-status page and correcting their path, timer, provenance, and snapshot
wiring.

## Core guarantees

- Invalid or incomplete runs are retained but cannot advance advertisement state.
- Absence, filter exit, detail availability, sale evidence, and repost identity are
  separate concepts.
- Detail checks prove that a page was examined; content snapshots are deduplicated.
- Manual and synthetic runs never become genuine scheduled history.
- Scheduling uses an explicit IANA timezone and records missed/catch-up slots honestly.
- SQLite and PostgreSQL share the same logical migration history.
- Reports retain run IDs, timestamps, configuration hash, and component versions.

The full command inventory, including `snapshot` and `history`, is maintained in the
[CLI reference](docs/reference/cli-and-configuration.md#monitor) and checked against
the argparse parser.
