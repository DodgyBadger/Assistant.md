"""Markdown template loading for the experimental Monty-backed authoring surface."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.authoring.registry import reject_retired_helper_dependencies
from core.utils.frontmatter import parse_simple_frontmatter

_PYTHON_BLOCK_PATTERN = re.compile(
    r"```python\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE
)


@dataclass(frozen=True)
class AuthoringTemplateSource:
    """Parsed markdown template source for Monty execution."""

    frontmatter: dict[str, Any]
    code: str
    block_count: int
    docstring_summary: str | None
    body: str


def load_authoring_template_file(file_path: str | Path) -> AuthoringTemplateSource:
    """Read and parse one markdown template file."""
    content = Path(file_path).read_text(encoding="utf-8")
    return parse_authoring_template_text(content)


def parse_authoring_template_text(content: str) -> AuthoringTemplateSource:
    """Parse frontmatter and exactly one fenced python block from markdown text."""
    frontmatter, body = parse_simple_frontmatter(content, require_frontmatter=False)
    matches = list(_PYTHON_BLOCK_PATTERN.finditer(body))
    if not matches:
        raise ValueError(
            "Authoring template must include exactly one fenced ```python``` block"
        )
    if len(matches) > 1:
        raise ValueError(
            "Authoring template supports exactly one fenced ```python``` block"
        )

    match = matches[0]
    code = match.group(1).strip()
    if not code:
        raise ValueError("Authoring template python block must not be empty")
    reject_retired_helper_dependencies(code)

    return AuthoringTemplateSource(
        frontmatter=frontmatter,
        code=code,
        block_count=1,
        docstring_summary=_extract_docstring_summary(code),
        body=body,
    )


def _extract_docstring_summary(code: str) -> str | None:
    """Extract the first line of a module docstring, when present."""
    try:
        module = ast.parse(code)
    except SyntaxError:
        return None

    docstring = ast.get_docstring(module)
    if not docstring:
        return None
    first_line = docstring.strip().splitlines()[0].strip()
    return first_line or None
