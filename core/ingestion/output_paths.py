from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from core.vault_state.file_mutations import vault_directory_mutation_lock


@dataclass(frozen=True)
class ImportOutputPaths:
    rel_dir: str
    base_name: str
    asset_dir: str
    markdown_path: str


def resolve_import_output_paths(
    *,
    path_pattern: str | None,
    relative_dir: str,
    source_filename: str | None,
    title: str | None,
    output_name: str | None = None,
) -> ImportOutputPaths:
    base_dir = "Imported/" if path_pattern is None else path_pattern
    rel_dir = "" if base_dir in {"", "."} else base_dir.rstrip("/")
    if relative_dir:
        rel_dir = os.path.join(rel_dir, relative_dir.strip("/"))

    filename = output_name
    if not filename:
        if source_filename:
            filename = Path(source_filename).stem
        if not filename:
            filename = title or "import"

        if source_filename and str(source_filename).startswith("http"):
            filename = _slugify(filename)
        else:
            filename = filename.replace("/", "_").replace("\\", "_").strip() or "import"

    asset_dir = os.path.join(rel_dir, "assets", filename).lstrip("/")
    markdown_path = os.path.join(rel_dir, f"{filename}.md").lstrip("/")
    return ImportOutputPaths(
        rel_dir=rel_dir,
        base_name=filename,
        asset_dir=asset_dir,
        markdown_path=markdown_path,
    )


@contextmanager
def allocate_import_output_paths(
    *,
    vault_root: Path,
    path_pattern: str | None,
    relative_dir: str,
    source_filename: str | None,
    title: str | None,
) -> Iterator[ImportOutputPaths]:
    """Reserve one collision-free namespace for an import artifact set."""
    desired = resolve_import_output_paths(
        path_pattern=path_pattern,
        relative_dir=relative_dir,
        source_filename=source_filename,
        title=title,
    )
    with vault_directory_mutation_lock(vault_root, vault_root / desired.rel_dir):
        counter = 0
        while True:
            output_name = (
                desired.base_name if counter == 0 else f"{desired.base_name}_{counter}"
            )
            candidate = resolve_import_output_paths(
                path_pattern=path_pattern,
                relative_dir=relative_dir,
                source_filename=source_filename,
                title=title,
                output_name=output_name,
            )
            if (
                not (vault_root / candidate.markdown_path).exists()
                and not (vault_root / candidate.asset_dir).exists()
            ):
                yield candidate
                return
            counter += 1


def _slugify(name: str) -> str:
    allowed = []
    for ch in name.lower():
        if ch.isalnum():
            allowed.append(ch)
        elif ch in (" ", "-", "_"):
            allowed.append("-")
    slug = "".join(allowed).strip("-")
    return slug or "import"
