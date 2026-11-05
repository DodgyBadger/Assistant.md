"""Translate explicit workspace library reads to the authenticated API."""

from hashlib import sha256
from uuid import uuid4

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
    operation_id = uuid4().hex
    identity = {
        "vault_name": vault_name,
        "workspace_path": workspace_path,
        "operation_id": operation_id,
    }
    logger.set_sinks(["validation"]).info(
        "Saved prompt discovery started",
        data={
            "event": "saved_prompt_discovery_started",
            "status": "started",
            **identity,
        },
    )
    try:
        library = read_workspace_prompts(root, workspace_path, filename)
    except PromptLibraryError as exc:
        logger.warning(
            "Saved prompt discovery failed",
            data={
                "event": "saved_prompt_discovery_failed",
                "status": "failed",
                **identity,
                "code": exc.code,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "issue": "saved_prompt_discovery_failed:"
                + sha256(
                    f"{vault_name}\0{workspace_path}\0{exc.code}".encode()
                ).hexdigest()[:24],
            },
        )
        raise APIException(
            exc.status_code, "SavedPromptsFailed", str(exc), {"code": exc.code}
        ) from exc
    logger.set_sinks(["validation"]).info(
        "Saved prompt discovery completed",
        data={
            "event": "saved_prompt_discovery_completed",
            "status": "completed",
            **identity,
            "workspace_path": library.workspace_path,
            "source_path": library.source_path,
            "library_status": library.status,
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
