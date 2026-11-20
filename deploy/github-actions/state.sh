#!/usr/bin/env bash
# Restore before calling the monitor; save after all of its writers have closed.
set -euo pipefail
umask 077
fail() { echo "$*" >&2; exit 1; }
[[ $# == 2 && ( $1 == restore || $1 == save ) && $2 == /* && $2 != / && ! -L $2 ]] ||
    fail "Usage: state.sh restore|save /absolute/state/directory"
mode=$1
state=$2
scripts=$(cd "$(dirname "$0")" && pwd)
mkdir -p -- "$state"
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

ssh_args=(-o BatchMode=yes -o StrictHostKeyChecking=yes)
if [[ -n ${BAMA_SSH_CONFIG:-} ]]; then
    [[ $BAMA_SSH_CONFIG =~ ^/[a-zA-Z0-9_./-]+$ ]] || fail "Invalid job SSH config path"
    ssh_args+=(-F "$BAMA_SSH_CONFIG")
fi
remote() {
    ssh "${ssh_args[@]}" bama-store bash -s -- "$@" < "$scripts/state-remote.sh"
}
transfer() {
    # No --inplace, times, modes, owners, ACLs or deletes. Files shared with
    # the prior generation are only replaced by rename when content changes.
    rsync -rc -e "ssh ${ssh_args[*]}" --rsync-path='umask 077 && rsync' "$@"
}
store=bama-store:bama-monitor-nationwide-v1
generation_re='^[0-9]+-[0-9]+$'

checkpoint() {
    python3 - "$1" "$state" "$scratch" <<'PY'
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

task, state_arg, scratch_arg = sys.argv[1:]
state = Path(state_arg)
scratch = Path(scratch_arg)
pointer = state / "snapshots/latest_valid.json"
target = None
if pointer.exists():
    latest = json.loads(pointer.read_text(encoding="utf-8"))
    target = latest["path"]
    run_id = latest["run_id"]
    if not isinstance(run_id, int) or run_id < 1 or not re.fullmatch(
        rf"snapshots/\d{{4}}-\d{{2}}-\d{{2}}/13-00_Europe-Paris/run_{run_id}", target
    ):
        raise ValueError("Invalid latest snapshot path or run ID")
if task == "path":
    print(target or "")
    sys.exit()

database = state / "monitor.sqlite"
if not database.is_file() or not database.stat().st_size:
    raise ValueError("Missing monitor database")
if task == "save":
    # SQLite backup includes committed WAL pages from the live database.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as src:
        database = scratch / "monitor.sqlite"
        with closing(sqlite3.connect(database)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
with closing(sqlite3.connect(database.as_uri() + "?immutable=1", uri=True)) as db:
    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    db.execute("SELECT 1 FROM monitoring_runs LIMIT 0")

files = {"monitor.sqlite": database}
if target:
    folder = state / target
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    names = manifest["files"]
    if (manifest["run_id"] != run_id or not isinstance(names, list) or not names
            or not all(re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9]+", n) for n in names)):
        raise ValueError("Invalid snapshot manifest")
    files["snapshots/latest_valid.json"] = pointer
    for name in [*names, "manifest.json"]:
        files[f"{target}/{name}"] = folder / name

lines = []
for relative, path in sorted(files.items()):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing regular checkpoint file {relative}")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    lines.append(f"{digest}  {relative}\n")
checksums = scratch / "CHECKSUMS"
if task == "save":
    checksums.write_text("".join(lines), encoding="ascii")
elif checksums.read_text(encoding="ascii") != "".join(lines):
    raise ValueError("Restored checkpoint checksum mismatch")
PY
}

if [[ $mode == restore ]]; then
    [[ -z $(find "$state" -mindepth 1 -maxdepth 1 -print -quit) ]] ||
        fail "Restore requires an empty state directory"
    base=$(remote head)
    [[ $base == empty || $base =~ $generation_re ]] || fail "Invalid store head"
    mkdir -p "$state"/{snapshots,reports,evidence}
    if [[ $base != empty ]]; then
        source="$store/generations/$base"
        transfer "$source/CHECKSUMS" "$scratch/"
        transfer --include='/monitor.sqlite' --include='/snapshots/' \
            --include='/snapshots/latest_valid.json' --exclude='*' "$source/" "$state/"
        target=$(checkpoint path)
        if [[ -n $target ]]; then
            transfer --relative "$source/./$target/" "$state/"
        fi
        checkpoint verify
    fi
    echo "$base" > "$state/.storage-base"
    echo "Restored storage generation $base"
    exit 0
fi

base=$(cat "$state/.storage-base")
[[ $base == empty || $base =~ $generation_re ]] || fail "Invalid restored base"
generation=${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}
[[ $generation =~ $generation_re ]] || fail "Invalid GitHub run or attempt"
checkpoint save
remote prepare "$generation" "$base"
destination="$store/.upload-$generation"
for tree in snapshots reports evidence; do
    transfer "$state/$tree/" "$destination/$tree/"
done
# Use the previous database as rsync's delta basis. --copy-dest also creates a
# separate regular file for an unchanged database, preserving the rollback copy.
transfer --copy-dest="../generations/$base" "$scratch/monitor.sqlite" "$scratch/CHECKSUMS" "$destination/"
remote commit "$generation" "$base"
echo "Published storage generation $generation"
