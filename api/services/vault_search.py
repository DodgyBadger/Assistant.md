"""Thin API orchestration for structured vault content search."""

from core.settings import get_file_search_timeout_seconds
from core.vault_state.search import VaultContentSearchResult, search_vault_content

from .vault_files import resolve_vault_root


def search_vault_text(
    *, vault_name: str, query: str, path: str = "", limit: int = 100
) -> VaultContentSearchResult:
    """Resolve the vault and run one bounded literal content search."""
    return search_vault_content(
        vault_path=resolve_vault_root(vault_name),
        query=query,
        path=path,
        limit=limit,
        timeout_seconds=get_file_search_timeout_seconds(),
    )
