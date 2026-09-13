#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
compose_file="${repository_root}/docker/advanced-shell/compose.smoke.yml"
smoke_log="${repository_root}/scripts/advanced_shell_smoke.latest.log"
rm -f -- "${smoke_log}"
touch "${smoke_log}"
exec > >(tee -a "${smoke_log}") 2>&1
echo "advanced-shell smoke log: ${smoke_log}"

smoke_root=$(mktemp -d "${TMPDIR:-/tmp}/advanced-shell-smoke.XXXXXX")
compose() {
    docker compose -f "${compose_file}" "$@"
}

cleanup() {
    local status=$?
    if [[ ${status} -ne 0 ]]; then
        echo "advanced-shell smoke container state:" >&2
        compose ps --all >&2 || true
        echo "advanced-shell smoke service logs:" >&2
        compose logs --no-color >&2 || true
    fi
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "${smoke_root}"
    if [[ -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
        chown "${SUDO_UID}:${SUDO_GID}" "${smoke_log}" || true
    fi
    return "${status}"
}
trap cleanup EXIT

fail() {
    echo "advanced-shell smoke failed: $*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is unavailable"
docker compose version >/dev/null 2>&1 || fail "docker compose is unavailable"
compose up -d --build --wait

ssh_options=(
    -F /dev/null
    -i /run/advanced-shell/client-identity/client_identity
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o UserKnownHostsFile=/run/advanced-shell/client-identity/known_hosts
    -o ConnectTimeout=5
    -p 2222
)
run_ssh() {
    compose exec -T client bash -c \
        'tail -f /dev/null | ssh "$@"' \
        -- "${ssh_options[@]}" advanced-shell@advanced-shell "$@"
}

stdout_path="${smoke_root}/stdout"
stderr_path="${smoke_root}/stderr"
set +e
run_ssh \
    "printf stdout-value; printf stderr-value >&2; exit 7" \
    >"${stdout_path}" 2>"${stderr_path}"
command_status=$?
set -e
[[ ${command_status} -eq 7 ]] || fail "remote exit status was ${command_status}"
[[ $(<"${stdout_path}") == "stdout-value" ]] || fail "stdout was not preserved"
[[ $(<"${stderr_path}") == "stderr-value" ]] || fail "stderr was not preserved"

run_ssh \
    "test ! -e /app/system && test ! -e /run/secrets && "\
"test ! -r /run/advanced-shell/host-identity/ssh_host_ed25519_key && "\
"touch /workspace/write-test"

run_ssh \
    "set -eu; "\
"command -v python uv uvx node npm npx bash git curl jq rg tar gzip xz unzip ps >/dev/null; "\
"python --version; uv --version; node --version; npm --version; jq --version; rg --version | head -n 1"

set +e
compose exec -T client bash -c \
    'tail -f /dev/null | ssh "$@"' \
    -- "${ssh_options[@]}" -tt advanced-shell@advanced-shell true >/dev/null 2>&1
pty_status=$?
set -e
[[ ${pty_status} -ne 0 ]] || fail "PTY allocation unexpectedly succeeded"

compose exec -T client bash -s -- "${ssh_options[@]}" <<'BASH'
set -euo pipefail
tail -f /dev/null | ssh "$@" \
    -L 127.0.0.1:19999:advanced-shell:2222 \
    advanced-shell@advanced-shell "sleep 5" >/tmp/forward.out 2>/tmp/forward.err &
forward_pid=$!
cleanup_forward() {
    kill "${forward_pid}" >/dev/null 2>&1 || true
    wait "${forward_pid}" >/dev/null 2>&1 || true
}
trap cleanup_forward EXIT
sleep 1
if timeout 2 bash -c '
    exec 3<>/dev/tcp/127.0.0.1/19999
    IFS= read -r banner <&3
    [[ ${banner} == SSH-* ]]
'; then
    echo "forwarded channel reached the advanced-shell SSH service" >&2
    exit 1
fi
BASH

compose exec -T client ssh-keygen -q -t ed25519 -N '' -f /tmp/unauthorized_client
set +e
compose exec -T client ssh \
    -F /dev/null \
    -i /tmp/unauthorized_client \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/run/advanced-shell/client-identity/known_hosts \
    -o ConnectTimeout=5 \
    -p 2222 \
    advanced-shell@advanced-shell true >/dev/null 2>&1
unauthorized_status=$?
set -e
[[ ${unauthorized_status} -ne 0 ]] || fail "unauthorized SSH key succeeded"

set +e
compose exec -T client timeout 2 bash -c \
    'tail -f /dev/null | ssh "$@"' \
    -- "${ssh_options[@]}" advanced-shell@advanced-shell \
    "python -c 'import os, signal, time; child=os.fork(); os.setsid() if child == 0 else None; open(\"/workspace/stubborn.pid\", \"w\").write(str(os.getpid())) if child == 0 else None; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(300)'" \
    >/dev/null 2>&1
set -e
sleep 4
run_ssh \
    "pid=\$(cat /workspace/stubborn.pid); ! kill -0 \"\${pid}\" 2>/dev/null"

advanced_shell_container_id=$(compose ps -q advanced-shell)
published_ports=$(
    docker inspect --format \
        '{{range $bindings := .NetworkSettings.Ports}}{{range $bindings}}{{.HostIp}}:{{.HostPort}}{{end}}{{end}}' \
        "${advanced_shell_container_id}"
)
[[ -z ${published_ports} ]] || fail "advanced-shell SSH port is published"

echo "advanced shell Docker smoke passed"
