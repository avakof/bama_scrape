# Configuration defaults

This reference inventories the Pydantic configuration models in the current
revision. The models are the authority when this page and code disagree.

For base, deep, and monitor commands, `--config` appears before the subcommand.
For EDA it appears after the subcommand. Precedence is explicit CLI value, YAML
value, then model default. Unknown YAML keys fail validation, but a missing YAML path
currently falls back silently to defaults.

“CLI” below means that the public parser exposes a direct override. A field can be
valid YAML without having a flag.

## Base scraper (`Config`)

| Field | Default | CLI | Meaning |
|---|---|---:|---|
| `url` | repository reference search | yes | Exact saved-search URL |
| `mode` | `auto` | yes | `auto`, `api`, `pagination`, or `scroll` |
| `headed` | `false` | yes | Show the Playwright browser |
| `user_agent` | Chrome-like desktop UA | no | HTTP/browser User-Agent |
| `locale` | `fa-IR` | no | Browser locale |
| `viewport_width` | `1440` | no | Browser viewport pixels |
| `viewport_height` | `900` | no | Browser viewport pixels |
| `max_scrolls` | `2000` | yes | Hard scroll-cycle ceiling |
| `stale_cycles` | `8` | yes | Consecutive no-progress scroll cycles |
| `max_runtime` | `7200.0` | yes | Per-stage time ceiling, seconds |
| `scroll_pause_ms` | `1200` | no | Delay after scrolling, milliseconds |
| `settle_ms` | `2500` | no | Navigation/load-more settle delay, milliseconds |
| `api_page_size` | `30` | no | Search endpoint page size; observed maximum is 30 |
| `api_stale_pages` | `2` | no | Consecutive empty API pages before termination |
| `concurrency` | `2` | yes | Detail-fetch worker ceiling |
| `delay_min` | `0.4` | yes | Minimum randomized request delay, seconds |
| `delay_max` | `1.2` | yes | Maximum randomized request delay, seconds |
| `max_retries` | `4` | no | Bounded request attempts |
| `request_timeout` | `45.0` | no | HTTP timeout, seconds |
| `navigation_timeout` | `60.0` | no | Browser navigation timeout, seconds |
| `recycle_context_every` | `200` | no | HTTP/browser recycle interval |
| `resume` | `true` | enable-only | Skip completed detail rows |
| `refresh` | `false` | enable-only | Re-fetch completed rows |
| `download_images` | `false` | enable-only | Download image bytes in base detail collection |
| `output_dir` | `output` | yes | SQLite, export, raw, and debug root |
| `log_level` | `INFO` | yes | Structured log threshold |
| `save_failed_html` | `true` | no | Save failed/ambiguous HTML under output |

The model also has an internal `extra` mapping defaulting to `{}`. Unknown top-level
YAML fields are still forbidden; this mapping is not an escape hatch for arbitrary
configuration.

## Deep scraper (`DeepConfig`)

| Field | Default | CLI | Meaning |
|---|---|---:|---|
| `source_db` | `output/bama_ads.sqlite` | yes | Base database opened as seed input |
| `output_dir` | `deep_scraper/data` | yes | Deep database and export root |
| `concurrency` | `2` | yes | Shared request semaphore ceiling |
| `delay_min` | `0.4` | yes | Minimum randomized delay, seconds |
| `delay_max` | `1.2` | yes | Maximum randomized delay, seconds |
| `max_retries` | `4` | yes | Per-entity phase retry ceiling; transport retry count is fixed separately |
| `request_timeout` | `45.0` | yes | HTTP timeout, seconds |
| `recycle_context_every` | `200` | no | HTTP client recycle interval measured in successful fetches |
| `user_agent` | Chrome-like desktop UA | no | HTTP User-Agent |
| `referer` | repository reference search | no | HTTP Referer |
| `max_runtime` | `10800.0` | yes | Per-phase time ceiling, seconds |
| `max_requests` | `8000` | yes | Successful-fetch ceiling; failed/retried wire attempts are not counted |
| `phases` | `a,b,c,d` | yes | Enabled phase letters |
| `skip_api` | `false` | enable-only | Skip phase-A JSON source |
| `skip_html` | `false` | enable-only | Skip phase-A HTML source |
| `refresh` | `false` | enable-only | Refresh completed work from a fixed snapshot |
| `retry_delisted` | `false` | enable-only | Recheck terminal delisted advertisements |
| `download_images` | `false` | enable-only | Accepted but currently not implemented |
| `dry_run` | `false` | enable-only | Implemented only by standalone `ads`; use `plan` for safety |
| `max_critical_conflict_rate` | `0.01` | no | Audit failure threshold among live ads |
| `raw_description` | `false` | no | Accepted but currently unused |
| `no_dealer_address` | `false` | enable-only | Omit dealer address from exports |
| `log_level` | `INFO` | yes | Log threshold |
| `json_logs` | `false` | no | Accepted but currently not passed to the logging setup |

The deep model also carries an internal `extra` mapping. As with the base model,
unknown YAML keys remain forbidden.

## Monitor (`MonitorConfig`)

Paths shown as “repository …” are computed from the installed source tree, not from
the caller's current directory.

### Top-level

| Field | Default | CLI | Meaning |
|---|---|---:|---|
| `search_url` | repository reference search | yes | Saved search monitored every run |
| `timezone` | `Europe/Paris` | yes | IANA schedule zone |
| `run_at` | `13:00` | yes | Local wall-clock slot |
| `database_url` | `BAMA_MONITOR_DATABASE_URL` or repository SQLite | yes | Monitoring database |
| `removal_confirmation_misses` | `2` | yes | Valid misses required for removal state |
| `persistent_absence_days` | `7` | no | Accepted and hashed, but scoring currently hard-codes seven days |
| `entry_tolerance_hours` | `36.0` | no | Publication-to-first-seen limit for full-life eligibility |
| `output_dir` | repository `monitor_data` | yes | Per-run evidence and monitor scratch data |
| `daily_snapshot_dir` | repository `daily_snapshots` | no | Immutable snapshot publication root |
| `production_schedule_name` | `bama-daily-1300-paris` | run-daily/daemon | Slot-uniqueness namespace |
| `catch_up_grace_minutes` | `240` | no | Maximum honest catch-up delay |
| `watchdog_interval_seconds` | `60` | no | Wall-clock reconciliation interval |
| `on_time_grace_minutes` | `10` | no | Scheduled-versus-catch-up classification boundary |
| `reports_dir` | repository `reports` | yes | Daily report root |
| `lock_dir` | repository `monitor_data/locks` | no | Filesystem lock root |
| `max_discovery_pages` | `5000` | no | Discovery page ceiling |
| `max_run_seconds` | `10800.0` | no | Discovery time ceiling; not an overall pipeline deadline |
| `dry_run` | `false` | enable-only | Accepted but currently unused; not a no-network guard |
| `log_level` | `INFO` | yes | Log threshold |

The scheduler daemon exposes direct overrides for database URL, timezone, run time,
and production schedule name, plus `--once` and `--print-next`. Its catch-up,
watchdog, and on-time-grace settings are YAML-only. The normal monitor command parser
exposes only the fields marked above.

### Run health (`health`)

| Field | Default | Meaning |
|---|---:|---|
| `min_count_ratio_vs_median` | `0.30` | Minimum inventory fraction relative to recent median |
| `median_window_runs` | `7` | Valid runs used for the baseline median |
| `min_absolute_count` | `50` | Absolute inventory floor |
| `max_failed_page_ratio` | `0.20` | Maximum result-page failure share |
| `max_empty_title_ratio` | `0.20` | Maximum empty-card-title share |
| `max_missing_ratio` | `0.50` | Maximum known inventory marked absent by one run |
| `require_verified_termination` | `true` | Require an observed verified endpoint end |
| `require_stabilization` | `true` | Require discovery stabilization evidence |

### Sale evidence (`scoring`)

| Field | Default |
|---|---:|
| `absent_two_valid_runs` | `0.30` |
| `detail_explicitly_unavailable` | `0.15` |
| `detail_persistent_404_410` | `0.15` |
| `recent_price_reduction` | `0.10` |
| `disappeared_soon_after_publication` | `0.10` |
| `seller_did_not_repost` | `0.10` |
| `disappearance_persists_seven_days` | `0.10` |
| `reappeared` | `-0.30` |
| `vehicle_reposted` | `-0.25` |
| `detail_remains_accessible` | `-0.20` |
| `appears_outside_original_filter` | `-0.20` |
| `possibly_sold_at` | `0.40` |
| `likely_sold_at` | `0.65` |
| `highly_likely_sold_at` | `0.85` |
| `ranking_min_confidence` | `0.65` |
| `status_promotion_at` | `0.65` |
| `soon_after_publication_days` | `3.0` |

Positive weights are evidence consistent with a sale; negative weights are evidence
against. The sum is a heuristic rank, not a probability.

### Repost matching (`repost`)

| Field | Default |
|---|---:|
| `enabled` | `true` |
| `lookback_days` | `45` |
| `mileage_tolerance_km` | `5000` |
| `mileage_bucket_km` | `10000` |
| `price_tolerance_ratio` | `0.12` |
| `price_bucket_toman` | `50000000` (accepted but currently unused) |
| `description_similarity_min` | `0.72` |
| `match_threshold` | `0.70` |
| `weight_brand_model_year` | `0.30` |
| `weight_same_seller` | `0.20` |
| `weight_mileage_close` | `0.15` |
| `weight_price_close` | `0.10` |
| `weight_city` | `0.05` |
| `weight_image_hash` | `0.20` |
| `weight_description_similar` | `0.15` |
| `weight_colors` | `0.05` |

The so-called image hash is a digest of normalized CDN URL paths, not a perceptual
hash of image pixels.

### Detail policy (`detail`)

| Field | Default | Meaning |
|---|---:|---|
| `concurrency` | `2` | Fetcher semaphore setting; current census loop is serial |
| `scrape_new` | `true` | Select new ads under selective policy |
| `scrape_card_changed` | `true` | Select card changes under selective policy |
| `scrape_missing` | `true` | Check absent ads |
| `scrape_previous_failures` | `true` | Recheck prior failures |
| `active_refresh_days_min` | `3` | Accepted but currently unused |
| `active_refresh_days_max` | `7` | Actual stale-snapshot refresh cutoff |
| `qc_sample_ratio` | `0.01` | Random quality-control sample share |
| `max_details_per_run` | `600` | Selective-policy cap; ignored by full census |
| `check_every_advertisement_daily` | `true` | Full detail census switch |
| `delay_min` | `0.4` | Minimum randomized delay, seconds |
| `delay_max` | `1.2` | Maximum randomized delay, seconds |
| `request_timeout` | `45.0` | Detail request timeout, seconds |

### Alerts (`alerts`)

| Field | Default |
|---|---|
| `enabled` | `true` |
| `webhook_url_env` | `BAMA_MONITOR_WEBHOOK_URL` |
| `email_to_env` | `BAMA_MONITOR_ALERT_EMAIL` |
| `smtp_url_env` | `BAMA_MONITOR_SMTP_URL` |
| `min_severity` | `warning` |

Alert rows are persisted before this dispatch policy is applied. `enabled=false` or
a higher `min_severity` suppresses sink delivery; it does not erase the alert record.

## EDA (`EdaConfig`)

### Top-level

| Field | Default | CLI | Meaning |
|---|---|---:|---|
| `database_url` | EDA/generic/monitor env fallback, then repository SQLite | yes | Source monitoring database |
| `run_id` | `latest-valid` | yes | Selected monitoring run |
| `output_dir` | repository `reports/eda` | yes | Analysis output root |
| `cache_dir` | repository `reports/eda/.cache` | no | Directory is created, but no current analysis cache consumes it |
| `include_left_truncated` | `false` | enable-only | Accepted and recorded, but does not currently change exclusion behavior |
| `html` | `true` | yes/no | Generate HTML for `run`/`report` |
| `export_csv` | `true` | yes/no | Generate CSV derivatives |
| `export_parquet` | `true` | yes/no | Generate Parquet derivatives |
| `random_seed` | `20260728` | no | Deterministic sampling/jitter seed |
| `max_rows_in_memory` | `500000` | no | Accepted but not currently wired into dataset construction |
| `chunk_size` | `50000` | no | Accepted but not currently wired into dataset construction |
| `log_level` | `INFO` | yes | Accepted but no EDA logging setup currently consumes it |

Database environment fallback order is `BAMA_EDA_DATABASE_URL`,
`BAMA_DATABASE_URL`, `BAMA_MONITOR_DATABASE_URL`, then local SQLite.

### Thresholds (`thresholds`)

| Field | Default | CLI |
|---|---:|---:|
| `min_group_size` | `10` | yes |
| `min_survival_group_size` | `30` | yes |
| `min_survival_events` | `10` | yes |
| `snapshot_stale_hours` | `72.0` | yes |
| `weak_correlation` | `0.2` | no |
| `min_calibration_labels` | `40` | no |
| `min_correlation_pairs` | `30` | no |

### Optional enrichment (`enrichment`)

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Opt into the deep database join |
| `database_path` | `null` | Deep SQLite path; CLI enablement supplies the repository default |
| `table` | `ad_deep` | Listing-attribute source table |
| `join_left` | `platform_ad_id` | Monitoring-side key |
| `join_right` | `ad_id` | Deep-side key |
| `timestamp_column` | `scraped_at` | Temporal cutoff column |
| `include_all_columns` | `true` | Include every eligible analytical column |
| `min_column_coverage` | `0.01` | Drop enrichment columns below this coverage |
| `include_specs` | `true` | Join trim-grain generated specification view |
| `spec_view` | `v_trim_specs_wide` | Accepted but unused; code pivots `trim_specs` directly |
| `spec_join_column` | `review_key` | Trim/specification join key |

The CLI's `--enrich-attributes` switch enables enrichment and optionally accepts
`--enrichment-db`; other enrichment settings are YAML-only.

## Deployment overrides are not model defaults

[`config.monitor.example.yaml`](../../config.monitor.example.yaml) is the complete
local monitor example. `deploy/config.example.yaml` is a partial,
container-oriented override file. Its
`/data` paths do not match the native systemd service's write permissions, and it
does not set every model field. Do not infer omitted defaults or native deployment
safety from that example. Review
[Project status and known limitations](project-status-and-limitations.md#unattended-deployment-blockers)
before using it.
