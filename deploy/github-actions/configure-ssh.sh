#!/usr/bin/env bash
# Configure only the transport used by the daily monitor.
set -euo pipefail
umask 077

: "${HOSTINGER_HOST:?Set the hPanel SSH host in repository variables}"
: "${HOSTINGER_USER:?Set the hPanel SSH user in repository variables}"
: "${HOSTINGER_SSH_KEY:?Set the SSH private key in repository secrets}"
: "${HOSTINGER_HOST_FINGERPRINT:?Set the hPanel ED25519 server fingerprint in repository variables}"
port=${HOSTINGER_PORT:-65002}
if ! [[ $HOSTINGER_HOST =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ &&
        $HOSTINGER_USER =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ &&
        $port =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then
    echo 'Invalid hPanel host, user or port.' >&2
    exit 1
fi

dir=$(mktemp -d "$RUNNER_TEMP/bama-ssh.XXXXXXXX")
printf '%s\n' "$HOSTINGER_SSH_KEY" > "$dir/key"
# Verify every key that will be trusted, including multi-address DNS responses.
ssh-keyscan -T 10 -p "$port" -t ed25519 "$HOSTINGER_HOST" > "$dir/known_hosts" 2>/dev/null
fingerprints=$(ssh-keygen -E sha256 -lf "$dir/known_hosts" | awk '{print $2}' | sort -u)
[[ $fingerprints == "$HOSTINGER_HOST_FINGERPRINT" ]] || {
    echo 'The SSH server key differs from the ED25519 fingerprint configured from hPanel.' >&2
    exit 1
}
cat > "$dir/config" <<EOF
Host bama-store
    HostName $HOSTINGER_HOST
    Port $port
    User $HOSTINGER_USER
    IdentityFile "$dir/key"
    UserKnownHostsFile "$dir/known_hosts"
    GlobalKnownHostsFile /dev/null
    StrictHostKeyChecking yes
    IdentitiesOnly yes
    BatchMode yes
    ConnectTimeout 20
    ServerAliveInterval 30
    ServerAliveCountMax 4
EOF
printf 'BAMA_SSH_CONFIG=%s\n' "$dir/config" >> "$GITHUB_ENV"
