#!/usr/bin/env bash
set -u

# Manual Docker diagnostics; not part of the deterministic core suite.

repository_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd)
compose_file="${repository_root}/docker/advanced-shell/compose.development.yml"
if [[ -n ${CONTAINER_SYSTEM_ROOT:-} ]]; then
    state_root="${CONTAINER_SYSTEM_ROOT}/advanced-shell"
elif [[ -n ${ASSISTANTMD_DEV_RUNTIME_ROOT:-} ]]; then
    state_root="${ASSISTANTMD_DEV_RUNTIME_ROOT}/system/advanced-shell"
else
    state_root="${repository_root}/system/advanced-shell"
fi
mkdir -p "${repository_root}/validation/runs"
run_root=$(mktemp -d "${repository_root}/validation/runs/advanced-shell-diagnostics.XXXXXX")
diagnostic_log="${run_root}/diagnostics.log"

export ADVANCED_SHELL_CLIENT_PUBLIC_ROOT="${state_root}"
exec > >(tee "${diagnostic_log}") 2>&1

echo "compose state"
docker compose -f "${compose_file}" ps --all

echo "resolved client public root"
echo "${ADVANCED_SHELL_CLIENT_PUBLIC_ROOT}"

echo "resolved advanced-shell mounts"
docker compose -f "${compose_file}" config | sed -n '/advanced-shell:/,/^[^ ]/p' | sed -n '/volumes:/,/^[[:space:]]*[a-z_]*:/p'

echo "container health"
container_id=$(docker compose -f "${compose_file}" ps -a -q advanced-shell)
if [[ -n ${container_id} ]]; then
    echo "advanced-shell:"
    docker inspect --format '{{json .State}}' "${container_id}"
    echo "advanced-shell mounts:"
    docker inspect --format '{{json .Mounts}}' "${container_id}"
fi

echo "service logs"
docker compose -f "${compose_file}" logs --no-color advanced-shell

if [[ -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
    chown "${SUDO_UID}:${SUDO_GID}" "${diagnostic_log}" || true
fi

echo "wrote ${diagnostic_log}"
