"""Principal-owned encrypted secret storage."""

from .bootstrap import (
    SecretsBootstrapState,
    SecretsBootstrapStatus,
    get_encrypted_secrets_service,
    get_secrets_bootstrap_status,
    initialize_secrets_bootstrap,
    require_secrets_ready,
    reset_secrets_bootstrap_status,
)
from .crypto import SecretIntegrityError, SecretKeyring
from .service import (
    EncryptedSecretsService,
    SecretCopy,
    SecretGuardMismatchError,
    SecretIdentity,
    SecretMetadata,
    SecretMutationResult,
    SecretNamespaceDeletion,
    SecretRelocation,
    SecretWrite,
)

__all__ = [
    "EncryptedSecretsService",
    "SecretCopy",
    "SecretGuardMismatchError",
    "SecretsBootstrapState",
    "SecretsBootstrapStatus",
    "SecretIntegrityError",
    "SecretKeyring",
    "SecretIdentity",
    "SecretMetadata",
    "SecretMutationResult",
    "SecretNamespaceDeletion",
    "SecretRelocation",
    "SecretWrite",
    "get_secrets_bootstrap_status",
    "get_encrypted_secrets_service",
    "initialize_secrets_bootstrap",
    "require_secrets_ready",
    "reset_secrets_bootstrap_status",
]
