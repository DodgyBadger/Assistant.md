#!/bin/sh
set -eu

identity_root=${ASSISTANTMD_SHELL_CLIENT_IDENTITY_ROOT:-/run/advanced-shell/client-identity}
client_public_root=${ASSISTANTMD_SHELL_CLIENT_PUBLIC_ROOT:-/run/advanced-shell/client-public}
host_public_root=${ASSISTANTMD_SHELL_HOST_PUBLIC_ROOT:-/run/advanced-shell/host-public}
identity_path="${identity_root}/client_identity"
published_key_path="${client_public_root}/client_identity.pub"
host_public_key_path="${host_public_root}/ssh_host_ed25519_key.pub"
known_hosts_path="${identity_root}/known_hosts"
host=${ASSISTANTMD_SHELL_HOST:-advanced-shell}
port=${ASSISTANTMD_SHELL_PORT:-2222}
alias=${ASSISTANTMD_SHELL_HOST_KEY_ALIAS:-}

execution_mode=${ASSISTANTMD_EXECUTION_MODE:-restricted}
compose_profiles=$(printf '%s' "${COMPOSE_PROFILES:-}" | tr -d '[:space:]')
case ",${compose_profiles}," in
    *,advanced,*)
        if [ "${execution_mode}" != advanced ]; then
            echo "COMPOSE_PROFILES includes advanced, so ASSISTANTMD_EXECUTION_MODE must also be advanced." >&2
            exit 1
        fi
        ;;
    *)
        if [ -n "${compose_profiles}" ] && [ "${execution_mode}" = advanced ]; then
            echo "ASSISTANTMD_EXECUTION_MODE is advanced, so COMPOSE_PROFILES must also include advanced for a Compose deployment." >&2
            exit 1
        fi
        ;;
esac

if [ "${execution_mode}" != advanced ]; then
    exec "$@"
fi

mkdir -p "${identity_root}" "${client_public_root}"
chmod 0700 "${identity_root}"

if [ ! -s "${identity_path}" ]; then
    ssh-keygen -q -t ed25519 -N '' -f "${identity_path}"
elif [ ! -s "${identity_path}.pub" ]; then
    ssh-keygen -y -f "${identity_path}" > "${identity_path}.pub"
fi
chmod 0600 "${identity_path}"
chmod 0644 "${identity_path}.pub"

derived_public=$(ssh-keygen -y -f "${identity_path}")
stored_public=$(sed -n '1p' "${identity_path}.pub")
[ "${derived_public}" = "${stored_public}" ] || {
    echo "Advanced shell client identity does not match its public key." >&2
    exit 1
}

client_public_temp="${published_key_path}.tmp.$$"
printf '%s\n' "${derived_public}" > "${client_public_temp}"
chmod 0644 "${client_public_temp}"
mv -f "${client_public_temp}" "${published_key_path}"

attempts=0
while [ ! -s "${host_public_key_path}" ] && [ "${attempts}" -lt 60 ]; do
    attempts=$((attempts + 1))
    sleep 1
done
if [ -s "${host_public_key_path}" ]; then
    host_public=$(sed -n '1p' "${host_public_key_path}")
    case "${host_public}" in
        ssh-ed25519\ *) ;;
        *)
            echo "Advanced-shell host key must be one SSH Ed25519 public key." >&2
            exit 1
            ;;
    esac
    if [ -z "${alias}" ]; then
        if [ "${port}" = 22 ]; then
            alias=${host}
        else
            alias="[${host}]:${port}"
        fi
    fi
    known_hosts_temp="${known_hosts_path}.tmp.$$"
    printf '%s %s\n' "${alias}" "${host_public}" > "${known_hosts_temp}"
    chmod 0644 "${known_hosts_temp}"
    mv -f "${known_hosts_temp}" "${known_hosts_path}"
else
    echo "Advanced-shell host public key is not available; AssistantMD will report trust as unavailable." >&2
fi

exec "$@"
