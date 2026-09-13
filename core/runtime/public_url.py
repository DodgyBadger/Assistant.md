"""Canonical externally reachable origin for this AssistantMD installation."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


class PublicUrlError(ValueError):
    """Raised when the configured public URL is not a safe application origin."""


@dataclass(frozen=True)
class PublicOrigin:
    """Validated canonical origin used to construct externally reachable URLs."""

    value: str

    @classmethod
    def parse(cls, value: str) -> PublicOrigin:
        """Parse and normalize an HTTPS or loopback-development origin."""
        raw = str(value or "").strip()
        if not raw:
            raise PublicUrlError("Assistant.md public URL cannot be empty.")
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as exc:
            raise PublicUrlError(
                "Assistant.md public URL has an invalid port."
            ) from exc
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        if scheme not in {"http", "https"} or hostname is None:
            raise PublicUrlError(
                "Assistant.md public URL must be an absolute HTTP or HTTPS origin."
            )
        if parsed.username is not None or parsed.password is not None:
            raise PublicUrlError("Assistant.md public URL cannot contain credentials.")
        if parsed.query or parsed.fragment:
            raise PublicUrlError(
                "Assistant.md public URL cannot contain a query string or fragment."
            )
        if parsed.path not in {"", "/"}:
            raise PublicUrlError(
                "Assistant.md public URL cannot contain an application path."
            )
        if scheme == "http" and not _is_loopback_host(hostname):
            raise PublicUrlError(
                "Assistant.md public URL requires HTTPS except on a loopback host."
            )
        normalized_host = _normalize_hostname(hostname)
        host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
        authority = f"{host}:{port}" if port is not None else host
        return cls(f"{scheme}://{authority}")

    def build_url(self, path: str) -> str:
        """Build one external URL without allowing the path to replace its origin."""
        raw_path = str(path or "")
        parsed = urlsplit(raw_path)
        decoded_path = unquote(parsed.path)
        if (
            not raw_path.startswith("/")
            or raw_path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or "\\" in decoded_path
            or any(part in {".", ".."} for part in decoded_path.split("/"))
            or any(ord(character) < 32 for character in decoded_path)
        ):
            raise PublicUrlError(
                "External application URLs require a safe absolute application path."
            )
        return f"{self.value}{parsed.path}"


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalize_hostname(hostname: str) -> str:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise PublicUrlError(
                "Assistant.md public URL has an invalid hostname."
            ) from exc
        if len(ascii_hostname) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in ascii_hostname.rstrip(".").split(".")
        ):
            raise PublicUrlError(
                "Assistant.md public URL has an invalid hostname."
            ) from None
        return ascii_hostname.rstrip(".")
    if isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None:
        raise PublicUrlError("Assistant.md public URL has an invalid hostname.")
    return address.compressed
