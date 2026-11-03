"""Ephemeral, genuinely verified TLS; private keys never leave the run directory."""

import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass
class TLSIdentity:
    certificate: Path
    key: Path
    client_context: ssl.SSLContext
    names: list = field(default_factory=list)


def local_tls(root: Path) -> TLSIdentity:
    root.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate = root / "localhost.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key = root / "localhost.key"
    private_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_key.chmod(0o600)
    context = ssl.create_default_context(cafile=str(certificate))
    return TLSIdentity(certificate, private_key, context)


def certificate_rejection(error):
    """Distinguish actual verification failures from unrelated connection errors."""
    pending = [error]
    seen = set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ssl.SSLCertVerificationError):
            return True
        pending.extend(e for e in (item.__cause__, item.__context__) if e is not None)
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions)
    return False
