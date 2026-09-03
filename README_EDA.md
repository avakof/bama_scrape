# Bama exploratory data analysis

`bama_eda` exposes a query-only API over a monitoring database, selects a run, builds
three analysis-ready datasets, audits their integrity, and writes statistics, charts,
manifests, and reports. The underlying connection is not transport-enforced
read-only; use database/filesystem permissions when that guarantee is required.

The source data records public asking prices and observed listing behavior. It does
not contain transaction prices or confirmed sale outcomes.

This compatibility page replaces the former long-form README. Use the canonical docs:

- [Run EDA](docs/how-to/analyze.md)
- [Methodology and censoring](docs/explanation/methodology.md)
- [Architecture](docs/explanation/architecture.md#eda)
- [EDA CLI and configuration](docs/reference/cli-and-configuration.md#eda)
- [Complete configuration defaults](docs/reference/configuration-defaults.md#eda-edaconfig)
- [Datasets and provenance](docs/reference/data-and-outputs.md#eda-datasets)
- [EDA troubleshooting](docs/reference/troubleshooting.md#eda)
- [Project status and known limitations](docs/reference/project-status-and-limitations.md#analysis-limitations)

## Quick start

```bash
pip install -e ".[eda]"
cp config.eda.example.yaml config.eda.yaml
python -m bama_eda inspect-schema --config config.eda.yaml
python -m bama_eda validate --config config.eda.yaml --run-id latest-valid
python -m bama_eda run --config config.eda.yaml --run-id latest-valid
```

EDA produces three grains that must not be conflated:

| Dataset | Grain |
|---|---|
| `advertisement_cross_section` | One advertisement at the selected run |
| `longitudinal_panel` | One monitoring run × advertisement |
| `vehicle_entity_dataset` | One inferred physical vehicle across reposts |

Optional attributes from the deep database require `--enrich-attributes`; the join
source, coverage, and temporal cutoff are recorded in `eda_manifest.json`.

`latest-valid` does not filter manual or synthetic triggers. Use monitor `history`
and an explicit run ID for production-only analysis.

The notebook at `notebooks/bama_eda_overview.ipynb` is intended as a thin viewer over
tested package functions, but its committed code-cell source is currently malformed.
Use the package CLI until the notebook is repaired and reviewed separately. No
analytical rule should live only in a notebook cell.
