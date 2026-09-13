"""Ingress-authentication configuration and identity contracts."""

from .middleware import (
    CSRF_HEADER,
    DEFAULT_PUBLIC_ROUTES,
    MAXIMUM_REQUEST_HEADER_BYTES,
    OWNER_CSRF_COOKIE,
    OWNER_SESSION_COOKIE,
    AuthenticationMiddleware,
    PublicRoute,
    get_authenticated_identity,
)
from .models import (
    AuthenticatedIdentity,
    AuthenticationMechanism,
    AuthenticationMode,
)
from .policy import (
    DEFAULT_PROXY_ASSERTION_HEADER,
    AuthenticationConfigurationError,
    AuthenticationPolicy,
    load_authentication_policy,
)
from .rate_limit import AuthenticationFailureLimiter
from .session import (
    DEFAULT_SESSION_LIFETIME,
    IssuedOwnerSession,
    OwnerSessionCodec,
    SessionVerificationError,
    VerifiedOwnerSession,
)

__all__ = [
    "AuthenticatedIdentity",
    "AuthenticationConfigurationError",
    "AuthenticationFailureLimiter",
    "AuthenticationMechanism",
    "AuthenticationMiddleware",
    "AuthenticationMode",
    "AuthenticationPolicy",
    "DEFAULT_PROXY_ASSERTION_HEADER",
    "DEFAULT_PUBLIC_ROUTES",
    "DEFAULT_SESSION_LIFETIME",
    "IssuedOwnerSession",
    "MAXIMUM_REQUEST_HEADER_BYTES",
    "CSRF_HEADER",
    "OWNER_CSRF_COOKIE",
    "OWNER_SESSION_COOKIE",
    "OwnerSessionCodec",
    "PublicRoute",
    "SessionVerificationError",
    "VerifiedOwnerSession",
    "get_authenticated_identity",
    "load_authentication_policy",
]
