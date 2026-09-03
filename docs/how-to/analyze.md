# Run EDA

EDA's public API issues queries without intentional source-row or schema mutation,
selects one run, builds datasets at explicit grains, audits them, and writes
provenance beside every result. The underlying connection is not transport-enforced
read-only; use a read-only database role or filesystem permissions when required.

## Install and choose the source

```bash
pip install -e ".[eda]"
cp config.eda.example.yaml config.eda.yaml
```

For local SQLite:

```bash
python -m bama_eda inspect-schema \
  --database-url "sqlite:///monitor_data/bama_monitor.sqlite"
```

For PostgreSQL, supply the URL through `BAMA_EDA_DATABASE_URL`,
`BAMA_DATABASE_URL`, or `BAMA_MONITOR_DATABASE_URL`. Do not commit credentials.

Unlike the other command surfaces, EDA's `--config` belongs after the subcommand:

```bash
python -m bama_eda run --config config.eda.yaml --run-id latest-valid
```

## Inspect and validate first

```bash
python -m bama_eda inspect-schema --config config.eda.yaml
python -m bama_eda validate --config config.eda.yaml --run-id latest-valid
```

Schema inspection classifies tables and columns without assuming optional enrichment
exists. Validation builds the three datasets and fails nonzero on critical integrity
findings while still writing the quality report.

`latest-valid` requires a valid, finished, comparison-applied, non-empty run, but it
does not filter trigger provenance. A manual, deployment-test, backfill, or synthetic
run can be selected. For production-only analysis, inspect monitor `history` and pass
the intended numeric run ID explicitly.

## Build datasets or a full report

```bash
python -m bama_eda build-datasets \
  --config config.eda.yaml --run-id latest-valid

python -m bama_eda run \
  --config config.eda.yaml --run-id latest-valid --output-dir reports/eda
```

The three outputs are an advertisement cross-section, a run-by-advertisement panel,
and a vehicle-entity dataset. Do not join or aggregate them without preserving their
grain.

`report` reruns the analysis and regenerates report artifacts; it is not a formatter
over stale tables:

```bash
python -m bama_eda report --config config.eda.yaml --run-id latest-valid
```

Use `--no-html`, `--no-charts`, `--no-export-csv`, or `--no-export-parquet` when the
corresponding derivative is not needed. The manifest remains the provenance record.

## Compare inventories

```bash
python -m bama_eda compare-runs \
  --database-url "sqlite:///monitor_data/bama_monitor.sqlite" \
  --run-id-a 100 --run-id-b 101
```

This reports inventory differences between two explicit runs. It does not infer a
sale from a missing row.

## Optional deep attributes

```bash
python -m bama_eda run \
  --config config.eda.yaml \
  --run-id latest-valid \
  --enrich-attributes \
  --enrichment-db deep_scraper/data/bama_deep.sqlite
```

Enrichment is opt-in because it joins a second observation source. Only deep rows at
or before the selected monitoring run may be used. Joined columns, coverage, and
temporal policy are recorded in `eda_manifest.json`.

## Thresholds and censoring

Small groups remain in exported tables with an insufficient-sample label. Survival
curves require both enough subjects and enough observed events. Left-truncated rows
are currently excluded from duration rankings. The accepted
`--include-left-truncated` option is recorded in the fingerprint but does not yet
change that exclusion path, so do not rely on it to include those rows.

Read [Methodology](../explanation/methodology.md) before quoting a duration,
association, or sale score.

## Notebook and report artifacts

`notebooks/bama_eda_overview.ipynb` is intended to be a thin interactive viewer over
tested package functions, but its committed JSON currently omits source-line breaks
and does not compile as a notebook. Do not use it until that repair is reviewed and
committed separately. When repaired, configure its database through environment
variables or a relative URL; never paste credentials or machine-specific results
into the notebook.

The full report writes an `eda_manifest.json`, data-quality reports, schema/data
dictionaries, the three datasets, descriptive and inferential tables, warnings,
optional charts, and an optional HTML report. The longitudinal panel is Parquet-only
in the full report even when CSV export is enabled; the two smaller grains also get
CSV. Inspect the manifest before using any derivative.

See [Project status and known limitations](../reference/project-status-and-limitations.md#analysis-limitations)
for run-selection, connection, censoring, and enrichment boundaries.
