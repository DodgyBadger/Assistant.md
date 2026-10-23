#!/usr/bin/env python3
"""Check that new vault mutations use the shared mutation API."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SCAN_ROOTS = (
    Path("core/tools"),
    Path("core/authoring/helpers"),
    Path("core/ingestion"),
    Path("api"),
)

SHARED_MUTATION_API_FILES = {
    Path("core/vault_state/file_mutations.py"),
}

ALLOWED_DIRECT_MUTATION_FILES = {
    Path("core/tools/workflow_run.py"),
}

ALLOWED_DIRECT_MUTATION_CALLS = {
    (
        Path("api/services/ingestion.py"),
        "_enqueue_import_scan_jobs",
        "import_root.mkdir",
    ),
    (
        Path("api/services/workflows.py"),
        "_write_system_workflow_file_content",
        "temp_path.write_text",
    ),
    (Path("core/ingestion/service.py"), "process_job", "import_root.mkdir"),
}

MUTATING_METHODS = {
    "mkdir",
    "write_text",
    "write_bytes",
    "unlink",
    "rename",
    "rmdir",
}

MUTATING_FUNCTIONS = {
    ("os", "makedirs"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "replace"),
    ("shutil", "move"),
    ("shutil", "rmtree"),
}


def main() -> int:
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path in SHARED_MUTATION_API_FILES:
                continue
            if path in ALLOWED_DIRECT_MUTATION_FILES:
                continue
            offenders.extend(_find_direct_mutations(path))

    if not offenders:
        print("Vault mutation routing check passed.")
        return 0

    print(
        "Vault mutation routing check failed.\n"
        "New vault-mutating code should route through core.vault_state.file_mutations.\n"
        "Direct mutation primitives were found:"
    )
    for offender in offenders:
        print(f"  - {offender}")
    return 1


def _find_direct_mutations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parents = _parent_map(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if (name == "open" or name.endswith(".open")) and _open_mode_is_mutating(node):
            if _is_allowed_direct_mutation(path, node, name, parents):
                continue
            offenders.append(f"{path}:{node.lineno}: open mutating mode")
        if _attribute_is_mutating_method(name):
            if _is_allowed_direct_mutation(path, node, name, parents):
                continue
            offenders.append(f"{path}:{node.lineno}: {name}")
        if any(
            name == f"{module}.{function}" for module, function in MUTATING_FUNCTIONS
        ):
            if _is_allowed_direct_mutation(path, node, name, parents):
                continue
            offenders.append(f"{path}:{node.lineno}: {name}")
    return offenders


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_allowed_direct_mutation(
    path: Path,
    node: ast.AST,
    call_name: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    function_name = _containing_function_name(node, parents)
    return (path, function_name, call_name) in ALLOWED_DIRECT_MUTATION_CALLS


def _containing_function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current.name
    return ""


def _attribute_is_mutating_method(name: str) -> bool:
    if not any(
        name == f".{method}" or name.endswith(f".{method}")
        for method in MUTATING_METHODS
    ):
        return False
    if name.endswith(".open"):
        return False
    return True


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value)
        if owner and not owner.startswith("."):
            return f"{owner}.{node.attr}"
        return f".{node.attr}"
    return ""


def _open_mode_is_mutating(node: ast.Call) -> bool:
    mode = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        mode = node.args[1].value
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = keyword.value.value
    if not isinstance(mode, str):
        return False
    return any(flag in mode for flag in ("w", "a", "x", "+"))


if __name__ == "__main__":
    sys.exit(main())
