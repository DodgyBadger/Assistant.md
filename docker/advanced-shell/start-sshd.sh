#!/bin/sh
set -eu

key_root=/run/advanced-shell
client_public_key_path="${key_root}/client-public/client_identity.pub"
host_identity_root="${key_root}/host-identity"
host_public_root="${key_root}/host-public"
host_key_path="${host_identity_root}/ssh_host_ed25519_key"
host_public_key_path="${host_public_root}/ssh_host_ed25519_key.pub"
authorized_keys_path=/run/sshd/authorized_keys
runtime_host_key_path=/run/sshd/ssh_host_ed25519_key

mkdir -p /run/sshd "${host_identity_root}" "${host_public_root}"
chmod 0700 "${host_identity_root}"

if [ ! -s "${host_key_path}" ]; then
    ssh-keygen -q -t ed25519 -N '' -f "${host_key_path}"
elif [ ! -s "${host_key_path}.pub" ]; then
    ssh-keygen -y -f "${host_key_path}" > "${host_key_path}.pub"
fi
chmod 0600 "${host_key_path}"
chmod 0644 "${host_key_path}.pub"

derived_host_public=$(ssh-keygen -y -f "${host_key_path}")
stored_host_public=$(sed -n '1p' "${host_key_path}.pub")
[ "${derived_host_public}" = "${stored_host_public}" ] || {
    echo "Advanced-shell host identity does not match its public key." >&2
    exit 1
}

host_public_temp="${host_public_key_path}.tmp.$$"
printf '%s\n' "${derived_host_public}" > "${host_public_temp}"
chmod 0644 "${host_public_temp}"
mv -f "${host_public_temp}" "${host_public_key_path}"

attempts=0
while [ ! -s "${client_public_key_path}" ] && [ "${attempts}" -lt 60 ]; do
    attempts=$((attempts + 1))
    sleep 1
done
if [ ! -s "${client_public_key_path}" ]; then
    echo "Missing AssistantMD client public key: ${client_public_key_path}" >&2
    exit 1
fi

public_key=$(sed -n '1p' "${client_public_key_path}")
case "${public_key}" in
    ssh-ed25519\ *) ;;
    *)
        echo "Advanced-shell client key must be one SSH Ed25519 public key." >&2
        exit 1
        ;;
esac

printf '%s %s\n' \
    'restrict,command="/usr/local/bin/advanced-shell-entry"' \
    "${public_key}" > "${authorized_keys_path}"

cp "${host_key_path}" "${runtime_host_key_path}"
chown root:root "${authorized_keys_path}" "${runtime_host_key_path}"
chmod 0644 "${authorized_keys_path}"
chmod 0600 "${runtime_host_key_path}"

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
