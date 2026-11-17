"""Shared outbound URL policy and log sanitization."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

from core.web.errors import WebUrlPolicyError

_HTTP_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def validate_public_url(url: str) -> str:
    """Validate HTTP(S) URL syntax and credentials; return its hostname, without DNS."""
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise WebUrlPolicyError("Only public http/https URLs are allowed")
    if parsed.username or parsed.password:
        raise WebUrlPolicyError("URLs containing credentials are not allowed")
    hostname = parsed.hostname
    if not hostname:
        raise WebUrlPolicyError("URL must include a hostname")

    return hostname


def resolve_public_url(url: str) -> tuple[str, tuple[str, ...]]:
    """Validate one HTTP(S) URL and return its hostname and public addresses."""
    hostname = validate_public_url(url)
    try:
        addresses = _resolve_addresses(hostname)
    except OSError as exc:
        raise WebUrlPolicyError(
            f"URL hostname could not be resolved: {hostname}"
        ) from exc
    if not addresses:
        raise WebUrlPolicyError(f"URL hostname could not be resolved: {hostname}")
    for address in addresses:
        if not _is_public_address(address):
            raise WebUrlPolicyError(
                f"Local or private network targets are not allowed: {hostname}"
            )
    return hostname, tuple(addresses)


def sanitize_url_for_log(url: str) -> str:
    """Remove credentials, query, and fragment from a URL used in logs."""
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError:
        return "invalid-url"
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return f"{scheme}:[redacted]" if scheme else "invalid-url"
    hostname = parsed.hostname or "unknown-host"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{hostname}:{port}" if port is not None else hostname
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


def sanitize_urls_in_text_for_log(text: str) -> str:
    """Sanitize HTTP(S) URLs embedded in diagnostic text."""
    return _HTTP_URL_IN_TEXT_RE.sub(
        lambda match: sanitize_url_for_log(match.group(0)),
        str(text or ""),
    )


def _resolve_addresses(hostname: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(str(info[4][0]) for info in infos))
    return [str(literal)]


def _is_public_address(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return bool(parsed.is_global)
