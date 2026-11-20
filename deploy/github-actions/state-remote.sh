#!/usr/bin/env bash
# Invoked on the storage host over SSH. Every caller uses this fixed private root.
set -euo pipefail
umask 077
cd "$HOME"
root=bama-monitor-nationwide-v1
generation_re='^[0-9]+-[0-9]+$'

fail() { echo "$*" >&2; exit 1; }
for utility in rsync sha256sum flock cp mv readlink; do
    command -v "$utility" >/dev/null || fail "Storage host requires $utility"
done
[[ -w . ]] || fail "SSH account home is not writable"

# The first upload creates a recognizable store with an explicit empty head.
# A later missing pointer is corruption and must never bootstrap a new series.
if [[ ! -e $root && ! -L $root ]]; then
    case $1 in
        head) echo empty; exit 0 ;;
        prepare)
            bootstrap=$(mktemp -d ".$root.XXXXXXXX")
            trap 'rm -rf -- "$bootstrap"' EXIT
            mkdir -p "$bootstrap/generations/empty"
            echo 1 > "$bootstrap/format"
            ln -s generations/empty "$bootstrap/current"
            mv -T -- "$bootstrap" "$root"
            trap - EXIT
            ;;
        *) fail "Store is missing" ;;
    esac
fi
[[ -d $root && ! -L $root ]] || fail "Unexpected storage root"
cd "$root"
[[ -f format && $(cat format) == 1 ]] || fail "Unrecognized storage layout"

# Serialize changes to the head and generation list. Transfers address a private
# staging directory; only the commit below can make its contents visible.
if [[ $1 != head ]]; then
    exec 9>publication.lock
    flock -x 9
fi
head=$(readlink current) || fail "Missing storage pointer"
base=${head#generations/}
[[ $head == "generations/$base" && ( $base == empty || $base =~ $generation_re ) ]] ||
    fail "Malformed storage pointer"
[[ -d $head && ! -L $head ]] || fail "Missing current generation"
if [[ $1 == head ]]; then
    echo "$base"
    exit 0
fi
[[ $# == 3 && $2 =~ $generation_re ]] || fail "Invalid generation arguments"
generation=$2
[[ $base == "$3" ]] || fail "Storage advanced after restore; retry from the current state"
next=generations/$generation
stage=.upload-$generation
[[ ! -e $next ]] || fail "Generation already exists"

case $1 in
    prepare)
        mkdir -- "$stage"
        for tree in snapshots reports evidence; do
            if [[ $base == empty ]]; then
                mkdir "$stage/$tree"
            else
                cp -al -- "$head/$tree" "$stage/$tree"
            fi
        done
        # This file belongs to its checkpoint. Other hard links are only ever
        # replaced by rsync's temporary-file rename, never written in place.
        rm -f -- "$stage/snapshots/latest_valid.json"
        ;;
    commit)
        [[ -d $stage && ! -L $stage ]] || fail "Missing staged upload"
        (cd "$stage" && sha256sum --check --status CHECKSUMS)
        mv -T -- "$stage" "$next"
        ln -s "$next" ".current-$generation"
        mv -Tf -- ".current-$generation" current
        # All published archive paths are in the new tree. Keep one rollback
        # generation; files common to both consume disk blocks only once.
        for old in generations/*; do
            if [[ ${old##*/} =~ $generation_re && $old != "$next" && $old != "$head" ]]; then
                rm -rf -- "$old"
            fi
        done
        # Reclaim abandoned uploads after two days, beyond the runner time limit.
        find . -maxdepth 1 -type d -name '.upload-[0-9]*-[0-9]*' -mtime +2 -exec rm -rf -- {} +
        ;;
    *) fail "Unknown storage operation" ;;
esac
