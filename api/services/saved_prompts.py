"""Translate explicit workspace library reads to the authenticated API."""

from core.chat.workspace_prompts import PromptLibraryError, read_workspace_prompts
from core.settings import get_general_settings

from ..exceptions import APIException
from ..models import SavedPromptInfo, SavedPromptsResponse
from .shared import logger
from .vault_files import resolve_vault_root


def get_saved_prompts(vault_name: str, workspace_path: str) -> SavedPromptsResponse:
    root = resolve_vault_root(vault_name)
    entry = get_general_settings().get("workspace_prompts_filename")
    filename = entry.value if entry is not None else "prompts.md"
    try:
        library = read_workspace_prompts(root, workspace_path, filename)
    except PromptLibraryError as exc:
        logger.warning(
            "Saved prompt discovery failed",
            data={"vault_name": vault_name, "code": exc.code},
        )
        raise APIException(
            exc.status_code, "SavedPromptsFailed", str(exc), {"code": exc.code}
        ) from exc
    logger.info(
        "Saved prompt discovery completed",
        data={
            "vault_name": vault_name,
            "workspace_path": library.workspace_path,
            "source_path": library.source_path,
            "status": library.status,
            "prompt_count": len(library.prompts),
        },
    )
    return SavedPromptsResponse(
        workspace_path=library.workspace_path,
        source_path=library.source_path,
        status=library.status,
        prompts=[
            SavedPromptInfo(id=p.id, title=p.title, body=p.body)
            for p in library.prompts
        ],
    )
