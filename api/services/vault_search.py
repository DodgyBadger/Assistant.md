"""Thin API orchestration for structured vault content search."""

from core.settings import get_file_search_timeout_seconds
from core.vault_state.favorites import favorite_paths
from core.vault_state.search import VaultContentSearchResult, search_vault_content

from .vault_files import resolve_vault_root


def search_vault_text(
    *,
    vault_name: str,
    query: str,
    path: str = "",
    limit: int = 100,
    favorites_only: bool = False,
) -> VaultContentSearchResult:
    """Resolve the vault and run one bounded literal content search."""
    root = resolve_vault_root(vault_name)
    return search_vault_content(
        vault_path=root,
        allowed_paths=favorite_paths(root) if favorites_only else None,
        query=query,
        path=path,
        limit=limit,
        timeout_seconds=get_file_search_timeout_seconds(),
    )
