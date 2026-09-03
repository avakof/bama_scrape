# Deep per-car scraper

`bama_deep` enriches the base scraper's discovered-ad inventory with public
advertisement API/HTML data, trim specifications, price history, and dealer
information. It merges independent sources, records conflicts, scrubs contact data,
and owns a separate resumable SQLite database.

This path remains a component landing page. Canonical documentation is available at:

- [Run deep enrichment](../docs/how-to/deep-enrichment.md)
- [Architecture](../docs/explanation/architecture.md#deep-enrichment)
- [Deep CLI and configuration](../docs/reference/cli-and-configuration.md#deep-scraper)
- [Deep data model](../docs/reference/data-and-outputs.md#deep-enrichment)
- [Deep troubleshooting](../docs/reference/troubleshooting.md#deep-scraper)
- [Responsible use and privacy](../docs/explanation/responsible-use.md)

## Quick start

```bash
pip install -e .
cp deep_scraper/config.example.yaml deep_scraper/config.yaml
python deep_scraper/run_deep.py --config deep_scraper/config.yaml seed
python deep_scraper/run_deep.py --config deep_scraper/config.yaml plan
python deep_scraper/run_deep.py --config deep_scraper/config.yaml run
```

`plan` estimates the request budget without issuing network requests. The all-in-one
run is resumable, but phase commands are preferable while validating a new target.
Do not substitute `--dry-run` for `plan`: the current dry-run guard applies only to
standalone `ads`.

## Phases

| Phase | Command | Grain |
|---|---|---|
| A | `ads` | Advertisement, combining API and HTML |
| B | `specs` | Model-trim technical specification |
| C | `prices` | Model-trim public price reference and date |
| D | `dealers` | Public dealer and dealer advertisement |

The authoritative database is `deep_scraper/data/bama_deep.sqlite`. Run `reindex`,
`audit`, and `export` after individual phases.

One shared fetcher enforces concurrency and the successful-fetch ceiling across all
selected phases of `run`. Failed and retried wire attempts are not included in that
counter, so it is not a strict HTTP-attempt cap. Stop on blocking responses; do not
add bypass behavior.

`download_images` and `raw_description` are accepted configuration fields but are
currently inert. See the
[project-status page](../docs/reference/project-status-and-limitations.md#accepted-but-incomplete-options)
for exact option boundaries.

`json_logs` is also accepted but is not passed into the deep CLI's logging setup in
this revision. The complete field matrix is in
[Configuration defaults](../docs/reference/configuration-defaults.md#deep-scraper-deepconfig).
