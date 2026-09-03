# Responsible use and privacy

Bama Scrape is designed for conservative collection of public listing data. It is
not an access-control bypass or a contact-data harvester.

## Collection boundary

The project may request public search results, advertisement pages, public JSON
endpoints used by those pages, public trim/specification material, public price
series, and public dealer profile information.

The intended design does not:

- authenticate as another user;
- bypass CAPTCHAs, access controls, or blocking responses;
- rotate identities to evade rate limits;
- call phone-reveal endpoints or intentionally collect dedicated phone fields;
- infer hidden contact details;
- treat deletion as proof of sale.

If Bama returns 403, 429, a CAPTCHA, or another blocking response, stop the run and
lower the request rate after waiting. Do not add evasion behavior.

## Request discipline

Defaults are deliberately conservative: low concurrency, randomized delays, bounded
retries, explicit time ceilings, resumable checkpoints, and request budgets. Review a
deep run with `plan` before issuing thousands of requests.

The absence of a `Crawl-delay` or a previously observed public endpoint is not a
permanent grant. Site behavior, terms, and law can change. The operator is responsible
for verifying current permission and applying stricter limits when needed.

## Contact-data handling by component

Detail payloads and seller-written descriptions can contain partially masked or
incidental contact data. The protections are not identical across the repository:

| Component | Protection | Residual risk |
|---|---|---|
| Base scraper | Recursively removes known contact keys before persisting parsed payloads | Seller-written descriptions are not pattern-redacted; the audit checks keys, not all contact-number formats |
| Deep scraper | Removes a broader key set and redacts supported Iranian phone, email, and messaging-handle patterns in selected free text | An unknown key or novel formatting can evade a pattern |
| Monitor | Uses deep advertisement parsers for detail snapshots | Public names, locations, descriptions, and media URLs remain potentially identifying |
| EDA | Scrubs contact-shaped free text again and audits output frames | Aggregates and exported identifiers still require a disclosure review |

The base database, its exports, captured HTML, screenshots, and logs must therefore
be treated as potentially containing personal data. “Publicly visible” is not the
same as “safe to republish.”

Dealer addresses are public business-premises data. Deep exports can omit that
column with `--no-dealer-address`. Private seller addresses are not an intended
collection target, but public neighbourhood/location labels and seller/dealer names
can still be personal or quasi-identifying data.

## Fixtures and repository hygiene

- Test fixtures must be sanitized before they enter version control.
- Never commit scraped databases, CSVs, JSONL, Parquet, reports, daily snapshots,
  logs, screenshots, failed HTML, local configuration, or `.env` files.
- Use synthetic records for new tests unless a sanitized structure is essential.
- Replace contact numbers, names, exact addresses, and unrelated free text in any
  captured fixture.
- Re-run the privacy audit after extending parsers or exports.

The repository `.gitignore` excludes major runtime-data paths, `.env` files, and
files named `config.yaml`, but it does not currently match `config.monitor.yaml` or
`config.eda.yaml`. Ignore rules are not a substitute for reviewing `git status`
before committing.

## Publication and redistribution

Collected listing content can be subject to database, copyright, privacy, contract,
and platform restrictions. This repository declares no software or data license.
Do not assume that public visibility permits bulk republication or commercial use.

For research publication, prefer aggregate statistics with run provenance, disclose
selection and censoring limits, and avoid examples that can identify an individual
seller.

## Security and secrets

Keep PostgreSQL URLs, webhook endpoints, SMTP credentials, and alert recipients in
environment files with restricted permissions. Supported environment variables are
listed in [CLI and configuration](../reference/cli-and-configuration.md#environment-variables-and-secrets).

The current macOS scheduler wrapper prints the configured database URL to its stdout
log. Do not put a password-bearing PostgreSQL URL into that environment until the
wrapper redacts it. The launchd installer also checks the default environment rather
than sourcing the configured environment file during its database preflight. These
are open deployment limitations, not merely documentation cautions.

Restore backups into a new database. The supplied restore workflow refuses to
overwrite an existing database because recovery should not destroy the history it is
meant to protect.

See [Project status and known limitations](../reference/project-status-and-limitations.md)
for the full deployment and privacy status.
