# Bama Scrape documentation

This is the canonical documentation for the complete repository. Choose the path
that matches the job you are trying to do.

## Start here

- [Getting started](getting-started.md) — install a clean environment and verify it.
- [Architecture](explanation/architecture.md) — understand the four systems and
  their databases.
- [CLI and configuration](reference/cli-and-configuration.md) — command, option,
  environment-variable, and configuration reference.
- [Configuration defaults](reference/configuration-defaults.md) — every model
  field, default, direct CLI override, and known inert setting.
- [Data and outputs](reference/data-and-outputs.md) — grains, tables, exports, units,
  and provenance.
- [Project status and known limitations](reference/project-status-and-limitations.md)
  — verified boundaries, incomplete options, privacy risks, and deployment blockers.

## How-to guides

- [Collect a one-time dataset](how-to/collect.md)
- [Run deep enrichment](how-to/deep-enrichment.md)
- [Operate the daily monitor](how-to/monitor.md)
- [Deploy, back up, and recover](how-to/deploy-and-recover.md)
- [Run EDA](how-to/analyze.md)

## Explanation

- [Architecture and data flow](explanation/architecture.md)
- [Monitoring and analytical methodology](explanation/methodology.md)
- [Responsible use and privacy](explanation/responsible-use.md)

## Reference and maintenance

- [CLI and configuration](reference/cli-and-configuration.md)
- [Configuration defaults](reference/configuration-defaults.md)
- [Data model and outputs](reference/data-and-outputs.md)
- [Project status and known limitations](reference/project-status-and-limitations.md)
- [Troubleshooting](reference/troubleshooting.md)
- [Contributing](contributing.md)
- [Dated evidence](evidence/README.md)

The root component files remain short compatibility landing pages for old links:
[`README_MONITOR.md`](../README_MONITOR.md),
[`README_EDA.md`](../README_EDA.md), and
[`deep_scraper/README.md`](../deep_scraper/README.md).

## Documentation rules

Durable behavior belongs in the how-to, explanation, or reference sections. Counts,
timings, observed endpoint behavior, operating-system versions, and deployment
experiments are time-sensitive and belong in `evidence/` with an observation date.

The command parsers, Pydantic configuration models, SQL migrations, and generated
manifests are the implementation sources of truth. This revision does not include a
tracked documentation-contract test; link/anchor resolution, command inventories,
example configuration loading, and notebook syntax must therefore be checked
explicitly. The known notebook failure is recorded in the project-status page.
