# 0044 - Bootstrap Advanced-Shell Trust With Container-Owned Identities

## Status

Accepted.

## Context

ADR 0042 places stdio MCP providers and general advanced execution behind a
fixed SSH connection to a separate container. That control channel must
authenticate AssistantMD to the advanced shell and pin the advanced shell's
identity. Trusting only private Docker networking would allow an unintended
network peer or replaced endpoint to impersonate either side.

Requiring operators to generate, distribute, quote, and preserve multiple SSH
keys would make the normal cross-platform setup unnecessarily fragile. Storing
private keys in `.env`, the AssistantMD system backup, a shared exchange volume,
or a general advanced-shell mount would expose infrastructure credentials to
broader inspection. A one-shot initializer or host provisioning script would
add another lifecycle component solely to exchange public material.

The identities authenticate one AssistantMD deployment to one advanced shell.
They are disposable infrastructure pairing state, not user credentials,
principal identity, application secrets, or data that must survive disaster
recovery.

## Decision

Have each long-running container generate and retain its own Ed25519 private
identity on first advanced-mode startup:

- AssistantMD owns the SSH client identity in an AssistantMD-only named volume.
- The advanced shell owns its SSH host identity in an advanced-shell-only named
  volume.

Exchange only public keys through two one-way named volumes. AssistantMD
publishes its client public key to a volume mounted read-only by the advanced
shell. The advanced shell publishes its host public key to a volume mounted
read-only by AssistantMD. Neither container receives the other container's
private identity, and neither consumer can modify the public material it reads.

Derive the advanced shell's restricted `authorized_keys` entry from the enrolled
client public key. Derive AssistantMD's pinned `known_hosts` entry from the
enrolled host public key. Verify that stored public keys match their local
private identities, use atomic publication, and fail closed when identity or
trust material is absent, malformed, or changed.

Persist the four pairing volumes across ordinary container recreation and image
upgrades. Treat removal of all pairing volumes followed by recreation of both
services as an explicit pairing reset and key rotation. Do not place these
private identities in `.env`, AssistantMD's `system/` directory, general bind
mounts, or the required backup set.

Externally provisioned identities may be supported as an advanced override, but
manual key generation, a host setup script, an initializer container, and
AssistantMD-mediated private-key exchange are not the default deployment flow.

## Consequences

- Normal advanced-mode startup automatically pairs the two services without
  exposing private keys across their container boundary.
- Pairing survives restart, recreation, and image upgrades independently of
  AssistantMD application data.
- Resetting the pairing requires coordinated volume deletion and recreation;
  deleting only part of the state is not a supported rotation procedure and can
  produce a trust mismatch depending on which identity material remains.
- Startup ordering requires bounded waits while the two services publish their
  public keys, and readiness must distinguish missing identity from missing or
  mismatched host trust.
- An operator able to replace Docker volumes can re-pair the services. Such an
  operator already controls the deployment boundary.
- Backup and restore instructions remain focused on vaults, `system/`, and
  `.env`; restored deployments can establish fresh infrastructure identities.

## Evidence

- Supported topology and pairing volumes: `docker-compose.yml`
- AssistantMD client bootstrap: `docker/bootstrap-advanced-shell-client.sh`
- Advanced-shell host bootstrap: `docker/advanced-shell/start-sshd.sh`
- Pinned readiness checks: `core/advanced_shell/preflight.py`
- Advanced-shell security contract: `docs/setup/security.md`
- Design and smoke-test record: `ADVANCED_MODE_SHELL_IMPLEMENTATION_PLAN.md`
