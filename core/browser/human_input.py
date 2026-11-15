"""Manually validated private input; never feed human values to Pydantic."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

InputKind = Literal["click", "text", "key", "scroll"]
KEYS = frozenset(
    {
        "Tab",
        "Enter",
        "Backspace",
        "Delete",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Home",
        "End",
        "Escape",
        "SelectAll",
    }
)
_TOKEN = re.compile(r"[a-f0-9]{32}\Z")


class BrowserInputError(RuntimeError):
    def __init__(
        self, reason: str, *, retire: bool = False, status_code: int = 409
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retire = retire
        self.status_code = status_code


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BrowserInputError("invalid_input", status_code=400)
        result[key] = value
    return result


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
        raise BrowserInputError("invalid_input", status_code=400)
    return value


@dataclass(frozen=True)
class HumanInput:
    page_id: str
    view_ref: str
    input_ref: str
    sequence: int
    kind: InputKind
    text: str = ""
    key: str = ""
    x: int = 0
    y: int = 0
    delta_y: int = 0

    @classmethod
    def decode(cls, raw: bytes) -> HumanInput:
        try:
            if len(raw) > 8192:
                raise BrowserInputError("input_too_large", status_code=413)
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
            if not isinstance(value, dict):
                raise ValueError("Invalid shape")
            common = {"page_id", "view_ref", "input_ref", "sequence", "kind"}
            tokens = [
                _text(value.get(key), 32)
                for key in ("page_id", "view_ref", "input_ref")
            ]
            page_id, view_ref, input_ref = tokens
            if not all(_TOKEN.fullmatch(token) for token in tokens):
                raise ValueError("Invalid reference")
            sequence = value.get("sequence")
            if type(sequence) is not int or not 1 <= sequence <= 1_000_000:
                raise ValueError("Invalid sequence")
            kind = value.get("kind")
            if kind == "text" and set(value) == common | {"text"}:
                text = _text(value["text"], 4096)
                if any(ord(char) < 32 and char != "\n" for char in text):
                    raise ValueError("Invalid text")
                return cls(page_id, view_ref, input_ref, sequence, "text", text=text)
            if kind == "key" and set(value) == common | {"key"}:
                key = _text(value["key"], 16)
                if key not in KEYS:
                    raise ValueError("Invalid key")
                return cls(page_id, view_ref, input_ref, sequence, "key", key=key)
            if kind == "click" and set(value) == common | {"x", "y"}:
                x, y = value["x"], value["y"]
                if (
                    type(x) is not int
                    or type(y) is not int
                    or not 0 <= x < 1600
                    or not 0 <= y < 1200
                ):
                    raise ValueError("Invalid coordinates")
                return cls(page_id, view_ref, input_ref, sequence, "click", x=x, y=y)
            if kind == "scroll" and set(value) == common | {"delta_y"}:
                delta = value["delta_y"]
                if type(delta) is not int or delta == 0 or not -800 <= delta <= 800:
                    raise ValueError("Invalid scroll")
                return cls(
                    page_id, view_ref, input_ref, sequence, "scroll", delta_y=delta
                )
            raise ValueError("Invalid command")
        except BrowserInputError:
            raise
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise BrowserInputError("invalid_input", status_code=400) from None
