# 0034 - Store Principal-Owned Secrets in Encrypted SQLite

## Status

Accepted.

## Context

AssistantMD currently stores provider, tool, and OAuth secret material in one
plaintext `system/secrets.yaml` file. That file cannot enforce principal
ownership and is unsuitable for MCP credentials and OAuth tokens. The product
is still single-user, but its backend already distinguishes `local-user` from
`system` execution and must preserve that boundary for a later multi-user
release.

The installation must remain low-friction and may run headlessly. Losing the
encryption key is recoverable by re-entering static secrets and reconnecting
OAuth accounts; AssistantMD does not need to provide key escrow or export.

## Decision

Replace the runtime YAML secret store with encrypted tables in `access.db` under
the configured system root. Register it as a managed system database. Every
secret record is owned by an explicit principal ID, including the reserved
`system` principal for operational credentials. Normal lookups derive ownership
from `ExecutionAuthority`; bootstrap and maintenance callers pass system scope
explicitly. Missing authority never defaults to `local-user`.

Use AES-256-GCM authenticated encryption from `cryptography`. Each write uses a
fresh 96-bit random nonce. Store the nonce and ciphertext (including the GCM
authentication tag) as SQLite BLOB values. Bind ciphertext to its identity with
canonical additional authenticated data containing:

- envelope format version;
- owner principal ID;
- namespace;
- secret name; and
- encryption-key version.

The canonical encoding is UTF-8, length-prefixed fields rather than delimiter
concatenation. Moving ciphertext to another owner, namespace, name, or key
version must fail authentication.

The record identity is `(owner_principal_id, namespace, name)`. Records also
contain envelope version, key version, nonce, ciphertext, and created/updated
timestamps. Secret values are UTF-8 text at the service boundary. Internal OAuth
namespaces are not returned by generic enumeration APIs.

Installation instructions require the user to generate a 32-byte key with a
cryptographically secure platform command and copy its unpadded URL-safe Base64
representation into `ASSISTANTMD_SECRETS_KEY`. A committed `.env.example`
supplies a placeholder only. The runtime treats this installation key as
internal key version `1`; encrypted records retain their key version so a safe
rotation workflow can be added later without changing the database format.

When the key is missing, malformed, does not decode to 32 bytes, or cannot
authenticate existing records, startup enters an explicit secrets-locked state.
The API and UI remain available for diagnosis, but model/provider execution and
secret mutation are disabled, the YAML import is not attempted or marked
complete, and encrypted state remains untouched. The System tab identifies the
recovery action but never includes key material, plaintext, ciphertext, OAuth
payloads, or credential values. There is no plaintext or newly generated-key
fallback.

AssistantMD does not currently provide key rotation. Users must retain the same
installation key while encrypted records exist.

AssistantMD does not expose encryption-key download, export, escrow, or managed
backup. Setup and recovery documentation warns that `.env` is plaintext key
material, database backups do not contain it, and losing it requires re-entry of
static secrets and OAuth reconnection. Users may back up `.env` using their own
deployment controls.

The existing `system/secrets.yaml` transition is a one-time, idempotent bootstrap
migration. It imports static values for `local-user`, assigns known operational
values such as `LOGFIRE_TOKEN` to `system`, does not import OAuth token/pending
state, authenticates every imported value, and retires the live YAML file only
after verification by renaming it to
`system/migration_backups/secrets.yaml.bak`. When that path exists, the importer
uses the next available numbered name, beginning with `secrets.yaml.bak (2)`,
and never overwrites an existing backup. Normal runtime code has no YAML fallback
after migration.

## Rationale

Authenticated encryption protects both confidentiality and record integrity.
Identity-bound additional authenticated data prevents a database edit from
reassigning ciphertext across principals or logical secret names. Separate key
versions allow safe rotation without embedding the master key in the database.

A principal-aware service makes authorization a storage invariant rather than a
UI convention. Keeping `local-user` as the only interactive resolver preserves
the current product while avoiding another secrets migration when multiple users
are introduced.

A documented one-command `.env` key setup fits the existing Compose installation
model without introducing a separate installer. Excluding key export and escrow
keeps AssistantMD out of the backup-key-management business; all encrypted
values are credentials that can be re-entered or reauthorized.

## Consequences

- `access.db` and `.env` must be backed up together to preserve encrypted
  credentials across disaster recovery.
- Database-only backups intentionally cannot decrypt secret values.
- Restoring a database with the wrong or missing key leaves secrets locked and
  disables model/provider execution until the matching keyring is restored.
- Model API keys and provider OAuth state become principal-owned even while the
  frontend exposes only `local-user`.
- Operational secrets remain explicitly system-owned and unavailable to user
  workflow authority.
- Validation runs require isolated system roots and generated keyrings instead
  of `SECRETS_PATH` overrides.
- All existing YAML consumers must move through the encrypted service before the
  plaintext runtime path can be removed.

## Evidence

- Implementation plan: `MCP_SUPPORT_IMPLEMENTATION_PLAN.md`
- Existing database ownership: ADR 0015
- Existing OAuth secret boundary: ADR 0022
- Existing execution-principal boundary: ADR 0028
- Current runtime contracts: `core/runtime/bootstrap.py`,
  `core/settings/secrets_store.py`, and `core/system_migrations.py`
