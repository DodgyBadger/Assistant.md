"""Plain UTF-8 content validation shared by direct vault text operations."""

from __future__ import annotations


class UnsupportedTextContent(ValueError):
    """The supplied bytes are not supported plain UTF-8 text."""


def decode_plain_text(content: bytes) -> str:
    """Decode plain text without lossy replacement or newline conversion."""
    # Some document/container signatures are valid UTF-8. Successful decoding
    # alone must not expose them to literal text edits.
    if content.lstrip().startswith(b"%PDF-") or content.startswith(
        (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"GIF87a", b"GIF89a")
    ):
        raise UnsupportedTextContent("Binary document content is not plain text.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedTextContent("Content is not UTF-8 text.") from exc
    if any(ord(character) < 32 and character not in "\t\n\r\f" for character in text):
        raise UnsupportedTextContent("Binary control characters are not plain text.")
    return text
