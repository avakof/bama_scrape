# Run the nationwide passenger-car monitor in Python

The collector covers Bama passenger cars across Iran from model year 1397 Jalali or 2018 Gregorian onward. It includes imports, domestic cars, dealers and private sellers. A listing qualifies when fresh evidence shows a full asking price strictly above 1,000,000,000 tomans. A price of exactly 1,000,000,000 tomans is excluded.

From the repository checkout, install the Python 3.11+ environment and run a collection.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[monitor]'
python -m bama_monitor.nationwide
```

The command prints the data directory and the final run status. Open `bama-nationwide-data/manual/snapshots/latest_valid.json` to find the latest validated export. Its `inventory.csv` and `inventory.parquet` files contain verified qualifying listings. The `eligibility.csv` file gives the decision and reason for each assessed listing, including exclusions and unresolved cases. The `run_summary.json` file records collection times, counts and validation checks. If validation fails, the command exits with a nonzero status and preserves the evidence for review.

To recover a listing's advertised features when it disappeared, match `advertisement_id` in its last qualifying `inventory.csv` to `platform_ad_id` in the event files. The first verified absence appears in `missing_ads.csv`. Two consecutive verified absences on valid runs produce a `removed_ads.csv` entry. A live listing repriced to the cutoff or below appears in `filter_exits.csv`. The transaction and its final price remain unverified.

For daily collection, keep the following process running on a machine with internet access.

```bash
python -m bama_monitor.nationwide --daily
```

The scheduled observation is due at **13:00 Europe/Paris**, including after daylight-saving changes. The scheduler records the actual observation times. On restart it reconciles outstanding slots. Within the configured four-hour grace period it can collect a late observation. Older unobserved dates are recorded as missed. Press **Ctrl+C** to finish the active observation and stop.

Daily history is stored under `bama-nationwide-data/daily/`, with `monitor.sqlite`, `evidence/`, and `snapshots/`. Each successful publication updates `snapshots/latest_valid.json`. Manual runs use their own database and snapshots. To keep the same history when starting from different working directories, pass the same absolute data path each time.

```bash
python -m bama_monitor.nationwide --daily --state-dir "$HOME/bama-data"
```

## Optional GitHub Actions schedule

The [daily workflow](../../.github/workflows/bama-daily.yml) can run the Python collector on GitHub and persist its history over SSH. Set the repository secret `HOSTINGER_SSH_KEY` and variables `HOSTINGER_USER` and `HOSTINGER_HOST_FINGERPRINT` (the server's ED25519 fingerprint). Set `HOSTINGER_PORT` if it differs from `65002`. Setting `HOSTINGER_HOST` enables scheduled collection. A manual workflow run checks storage access.

The workflow restores the database and latest snapshot before collection and publishes the results under `~/bama-monitor-nationwide-v1/current/` on the storage host.
