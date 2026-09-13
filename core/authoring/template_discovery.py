"""
Workflow and context template file discovery, validation, and loading.

Handles auto-discovery of authoring files from vault directories and
manages the WorkflowLoader used by the runtime scheduler.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.triggers.base import BaseTrigger

import core.authoring.engine as _engine
from core.constants import (
    ASSISTANTMD_ROOT_DIR,
    AUTHORING_DIR,
    SKILLS_DIR,
    VALID_WEEK_DAYS,
    VAULT_IGNORE_FILE,
)
from core.identity import LOCAL_USER_PRINCIPAL_ID
from core.logger import UnifiedLogger
from core.runtime.paths import get_data_root, get_system_root
from core.scheduling.parser import ScheduleParsingError, parse_schedule_syntax
from core.scheduling.triggers import create_schedule_trigger
from core.utils.frontmatter import parse_simple_frontmatter, upsert_frontmatter_key
from core.utils.hash import hash_file_content
from core.utils.markdown import parse_markdown_sections

logger = UnifiedLogger(tag="workflow-loader")

SEED_TEMPLATE_DIR = Path(__file__).parent / "seed_templates"


# ---------------------------------------------------------------------------
# WorkflowDefinition
# ---------------------------------------------------------------------------


@dataclass
class WorkflowDefinition:
    """Workflow configuration with fully parsed and validated objects."""

    vault: str
    name: str
    file_path: str
    trigger: BaseTrigger | None  # APScheduler trigger; None for manual-only
    schedule_string: str | None  # Original schedule string (for display)
    workflow_function: Callable
    run_type: str
    week_start_day: str
    description: str
    owner_principal_id: str = LOCAL_USER_PRINCIPAL_ID
    enabled: bool = False

    @property
    def global_id(self) -> str:
        return f"{self.vault}/{self.name}"

    @property
    def vault_path(self) -> str:
        return os.path.join(str(get_data_root()), self.vault)

    @property
    def scheduler_job_id(self) -> str:
        return self.global_id.replace("/", "__")

    @property
    def week_start_day_number(self) -> int:
        day_mapping = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        return day_mapping[self.week_start_day]


# ---------------------------------------------------------------------------
# TemplateRecord
# ---------------------------------------------------------------------------


@dataclass
class TemplateRecord:
    """Resolved context template with source metadata."""

    name: str
    content: str
    source: str  # "vault" or "system"
    path: Path | None
    sha256: str
    schema_block: str | None = None  # raw YAML/JSON block if present
    frontmatter: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Configuration error record
# ---------------------------------------------------------------------------


class ConfigurationError:
    """Represents a configuration error encountered during loading."""

    def __init__(
        self,
        vault: str,
        workflow_name: str | None,
        file_path: str,
        error_message: str,
        error_type: str,
        timestamp: datetime,
    ):
        self.vault = vault
        self.workflow_name = workflow_name
        self.file_path = file_path
        self.error_message = error_message
        self.error_type = error_type
        self.timestamp = timestamp


# ---------------------------------------------------------------------------
# File parsing helpers — shared
# ---------------------------------------------------------------------------


def _scan_md_files_one_level(directory: str) -> list[str]:
    """Scan for .md files directly in a directory and one level of non-underscore subfolders."""
    files = []
    for item in os.listdir(directory):
        item_path = os.path.join(directory, item)
        if item.endswith(".md") and os.path.isfile(item_path):
            files.append(item_path)
        elif os.path.isdir(item_path) and not item.startswith("_"):
            for subitem in os.listdir(item_path):
                if subitem.endswith(".md"):
                    subitem_path = os.path.join(item_path, subitem)
                    if os.path.isfile(subitem_path):
                        files.append(subitem_path)
    return files


def _discover_template_files(template_dir: Path) -> list[Path]:
    """
    Discover template files with one-level subfolder rules (Path-based variant).
    - Include *.md directly under the template root
    - Include *.md one level deep in subfolders not prefixed with underscore
    - Do not recurse deeper
    """
    if not template_dir.exists() or not template_dir.is_dir():
        return []

    template_files: list[Path] = []
    for item in sorted(template_dir.iterdir()):
        if item.is_file() and item.suffix.lower() == ".md":
            template_files.append(item)
            continue
        if item.is_dir() and not item.name.startswith("_"):
            for subitem in sorted(item.iterdir()):
                if subitem.is_file() and subitem.suffix.lower() == ".md":
                    template_files.append(subitem)

    return template_files


# ---------------------------------------------------------------------------
# Workflow file parsing helpers
# ---------------------------------------------------------------------------


def _parse_workflow_file(file_path: str) -> dict[str, Any]:
    """Parse workflow file; return sections dict with __FRONTMATTER_CONFIG__ key."""
    with open(file_path, encoding="utf-8") as fh:
        content = fh.read()
    frontmatter, remaining = parse_simple_frontmatter(
        content, require_frontmatter=False
    )
    sections: dict[str, Any] = parse_markdown_sections(remaining, "##")
    sections["__FRONTMATTER_CONFIG__"] = frontmatter
    return sections


def _validate_workflow_config(
    raw_config: dict[str, Any], vault: str, name: str
) -> dict[str, Any]:
    """Validate and normalise workflow frontmatter configuration."""
    run_type = str(raw_config.get("run_type") or "").strip().lower()
    if run_type != "workflow":
        if not run_type:
            raise ValueError(f"Missing required field 'run_type' in {vault}/{name}")
        raise ValueError(
            f"Invalid run_type '{run_type}' in {vault}/{name}. "
            "Workflow loading only accepts run_type 'workflow'."
        )

    schedule = raw_config.get("schedule")
    if schedule is not None:
        schedule = str(schedule).strip()
        try:
            parse_schedule_syntax(schedule)
        except ScheduleParsingError as exc:
            raise ValueError(f"Invalid schedule in {vault}/{name}: {exc}") from exc

    enabled = bool(raw_config.get("enabled", False))

    week_start_day = (
        str(
            raw_config.get("week_start_day")
            or raw_config.get("week-start-day")
            or "monday"
        )
        .strip()
        .lower()
    )
    if week_start_day not in VALID_WEEK_DAYS:
        raise ValueError(
            f"Invalid week_start_day '{week_start_day}' in {vault}/{name}. "
            f"Must be one of: {', '.join(VALID_WEEK_DAYS)}"
        )

    return {
        "run_type": run_type,
        "schedule": schedule,
        "enabled": enabled,
        "week_start_day": week_start_day,
        "description": str(raw_config.get("description") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Context template parsing helpers
# ---------------------------------------------------------------------------


def _ensure_md_suffix(name: str) -> str:
    """Normalize template names to include .md extension."""
    if not name.lower().endswith(".md"):
        return f"{name}.md"
    return name


def _hash_content(content: str) -> str:
    return hash_file_content(content, length=None)


def _resolve_template_path(template_dir: Path, normalized_name: str) -> Path | None:
    """Resolve a template path within one-level discovery scope."""
    normalized_key = normalized_name.replace("\\", "/")
    for path in _discover_template_files(template_dir):
        rel_key = path.relative_to(template_dir).as_posix()
        if rel_key == normalized_key:
            return path
    return None


def _extract_schema_block(content: str) -> str | None:
    """
    Extract first fenced block labeled yaml or json from the template.
    Returns raw block content or None.
    """
    lines = content.splitlines()
    in_block = False
    block_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not in_block:
            if stripped.startswith("```"):
                lang = stripped[3:].strip()
                if lang in ("yaml", "json", ""):
                    in_block = True
                    block_lines = []
            continue

        # inside block
        if stripped.startswith("```"):
            break
        block_lines.append(line)

    if not block_lines:
        return None
    return "\n".join(block_lines).strip()


def _read_template(path: Path, name: str, source: str) -> TemplateRecord:
    content = path.read_text(encoding="utf-8")
    frontmatter, _ = parse_simple_frontmatter(content)
    return TemplateRecord(
        name=name,
        content=content,
        source=source,
        path=path,
        sha256=_hash_content(content),
        schema_block=_extract_schema_block(content),
        frontmatter=frontmatter,
    )


# ---------------------------------------------------------------------------
# Vault / file discovery
# ---------------------------------------------------------------------------


def discover_vaults(data_root: str | None = None) -> list[str]:
    """Return sorted list of vault names from first-level directories."""
    if data_root is None:
        data_root = str(get_data_root())
    if not os.path.exists(data_root) or not os.path.isdir(data_root):
        return []
    vaults = []
    for item in os.listdir(data_root):
        item_path = os.path.join(data_root, item)
        if os.path.isdir(item_path) and not os.path.exists(
            os.path.join(item_path, VAULT_IGNORE_FILE)
        ):
            vaults.append(item)
    return sorted(vaults)


def ensure_vault_directories(vault_path: str) -> None:
    """Ensure all canonical AssistantMD subdirectories exist for a vault."""
    assistantmd_root = os.path.join(vault_path, ASSISTANTMD_ROOT_DIR)
    os.makedirs(os.path.join(assistantmd_root, AUTHORING_DIR), exist_ok=True)
    os.makedirs(os.path.join(assistantmd_root, SKILLS_DIR), exist_ok=True)
    _seed_vault_skills(vault_path)


def discover_workflow_files(vault_path: str) -> list[str]:
    """Return sorted workflow file paths from AssistantMD/Authoring."""
    ensure_vault_directories(vault_path)
    authoring_dir = os.path.join(vault_path, ASSISTANTMD_ROOT_DIR, AUTHORING_DIR)
    return sorted(_scan_md_files_one_level(authoring_dir))


def list_system_workflow_templates(
    system_root: Path | None = None,
) -> list[TemplateRecord]:
    """List workflow templates from system/Authoring without treating them as active workflows."""
    try:
        sys_root = system_root or get_system_root()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"System root unavailable while listing workflow templates: {exc}"
        )
        return []

    system_authoring_dir = Path(sys_root) / AUTHORING_DIR
    if not system_authoring_dir.exists():
        return []

    records: list[TemplateRecord] = []
    for path in _discover_template_files(system_authoring_dir):
        try:
            name = path.relative_to(system_authoring_dir).as_posix()
            record = _read_template(path, name, "system")
            if (record.frontmatter.get("run_type") or "").strip().lower() == "workflow":
                records.append(record)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Failed to read system workflow template {path}: {exc}")

    return records


# ---------------------------------------------------------------------------
# Workflow loading
# ---------------------------------------------------------------------------


def load_workflow_from_file(
    file_path: str,
    vault: str,
    name: str,
    validated_config: dict[str, Any],
    sections: dict[str, Any],
) -> WorkflowDefinition:
    """Load a WorkflowDefinition from a parsed configuration."""
    schedule_string = validated_config["schedule"]
    trigger = None
    if schedule_string is not None:
        trigger = create_schedule_trigger(parse_schedule_syntax(schedule_string))

    workflow_id = f"{vault}/{name}"
    _engine.validate_workflow_definition(
        workflow_id=workflow_id,
        file_path=file_path,
        sections=sections,
        validated_config=validated_config,
    )

    return WorkflowDefinition(
        vault=vault,
        name=name,
        file_path=file_path,
        trigger=trigger,
        schedule_string=schedule_string,
        workflow_function=_engine.run_workflow,
        run_type=validated_config["run_type"],
        week_start_day=validated_config["week_start_day"],
        description=validated_config["description"],
        enabled=validated_config["enabled"],
    )


def _workflow_name_from_vault_file_path(
    data_root: str, file_path: str
) -> tuple[str, str] | None:
    path_parts = file_path.replace(data_root, "").strip("/").split("/")
    if len(path_parts) < 4:
        return None
    if path_parts[1] != ASSISTANTMD_ROOT_DIR or path_parts[2] != AUTHORING_DIR:
        return None

    vault = path_parts[0]
    if len(path_parts) == 4:
        name = os.path.splitext(path_parts[3])[0]
    elif len(path_parts) == 5:
        name = f"{path_parts[3]}/{os.path.splitext(path_parts[4])[0]}"
    else:
        return None
    return vault, name


# ---------------------------------------------------------------------------
# Context template loading
# ---------------------------------------------------------------------------


def load_template(
    name: str | None,
    vault_path: Path | None,
    system_root: Path | None = None,
) -> TemplateRecord:
    """
    Resolve a context template by name with vault → system priority.

    Args:
        name: Template filename (required)
        vault_path: Path to the active vault (root). If None, vault lookup is skipped.
        system_root: Optional system root override (defaults to runtime system root).

    Returns:
        TemplateRecord describing the resolved template.
    """
    if not name:
        raise ValueError("Template name is required for load_template")

    normalized = _ensure_md_suffix(name)

    if vault_path:
        vault_authoring_dir = Path(vault_path) / ASSISTANTMD_ROOT_DIR / AUTHORING_DIR
        vault_authoring_template = _resolve_template_path(
            vault_authoring_dir, normalized
        )
        if vault_authoring_template is not None:
            record = _read_template(
                vault_authoring_template, normalized, source="vault"
            )
            if (record.frontmatter.get("run_type") or "").strip().lower() == "context":
                logger.info(f"Using vault template: {vault_authoring_template}")
                return record

    try:
        system_root = system_root or get_system_root()
    except Exception as exc:  # pragma: no cover - defensive: bootstrap not ready
        logger.warning(f"System root unavailable: {exc}")
        system_root = None

    if system_root:
        system_authoring_dir = Path(system_root) / AUTHORING_DIR
        system_authoring_template = _resolve_template_path(
            system_authoring_dir, normalized
        )
        if system_authoring_template is not None:
            record = _read_template(
                system_authoring_template, normalized, source="system"
            )
            if (record.frontmatter.get("run_type") or "").strip().lower() == "context":
                logger.info(f"Using system template: {system_authoring_template}")
                return record

    raise FileNotFoundError(
        f"Template '{normalized}' not found in vault or system templates"
    )


def list_templates(
    vault_path: Path | None,
    system_root: Path | None = None,
) -> list[TemplateRecord]:
    """List available context templates from vault and system locations."""
    records: list[TemplateRecord] = []

    if vault_path:
        vault_authoring_dir = Path(vault_path) / ASSISTANTMD_ROOT_DIR / AUTHORING_DIR
        if vault_authoring_dir.exists():
            for path in _discover_template_files(vault_authoring_dir):
                try:
                    name = path.relative_to(vault_authoring_dir).as_posix()
                    record = _read_template(path, name, "vault")
                    if (
                        record.frontmatter.get("run_type") or ""
                    ).strip().lower() == "context":
                        records.append(record)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(f"Failed to read vault template {path}: {exc}")

    try:
        sys_root = system_root or get_system_root()
    except Exception as exc:  # pragma: no cover
        logger.warning(f"System root unavailable while listing templates: {exc}")
        sys_root = None

    if sys_root:
        system_authoring_dir = Path(sys_root) / AUTHORING_DIR
        if system_authoring_dir.exists():
            for path in _discover_template_files(system_authoring_dir):
                try:
                    name = path.relative_to(system_authoring_dir).as_posix()
                    record = _read_template(path, name, "system")
                    if (
                        record.frontmatter.get("run_type") or ""
                    ).strip().lower() == "context":
                        records.append(record)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(f"Failed to read system template {path}: {exc}")

    return records


def seed_system_templates(
    system_root: Path | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    Seed system Authoring directory from seed_templates/context/ and seed_templates/workflows/.

    Startup should create missing files only. Explicit maintenance actions can
    pass overwrite=True to refresh existing system templates from packaged
    seeds.
    """
    result: dict[str, Any] = {
        "success": True,
        "created": [],
        "updated": [],
        "skipped": [],
        "errors": [],
    }
    try:
        sys_root = system_root or get_system_root()
    except Exception as exc:  # pragma: no cover - defensive during bootstrap
        logger.warning(f"System root unavailable while seeding templates: {exc}")
        result["success"] = False
        result["errors"].append(str(exc))
        return result

    if not SEED_TEMPLATE_DIR.exists():
        logger.warning(f"Seed template directory missing: {SEED_TEMPLATE_DIR}")
        result["success"] = False
        result["errors"].append(f"Seed template directory missing: {SEED_TEMPLATE_DIR}")
        return result

    target_dir = Path(sys_root) / AUTHORING_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    for subfolder in ("context", "workflows"):
        source_dir = SEED_TEMPLATE_DIR / subfolder
        if not source_dir.exists():
            continue
        for seed_path in sorted(source_dir.iterdir()):
            if not seed_path.is_file():
                continue
            target_path = target_dir / seed_path.name
            target_exists = target_path.exists()
            if target_exists and not overwrite:
                result["skipped"].append(str(target_path))
                continue
            try:
                content = seed_path.read_text(encoding="utf-8")
                if subfolder == "workflows" and target_exists and overwrite:
                    content = _preserve_enabled_frontmatter(
                        source_content=content,
                        existing_content=target_path.read_text(encoding="utf-8"),
                    )
                target_path.write_text(content, encoding="utf-8")
                result["updated" if target_exists else "created"].append(
                    str(target_path)
                )
                logger.info(f"Seeded template to {target_path}")
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Failed to seed template {target_path}: {exc}")
                result["success"] = False
                result["errors"].append(f"{target_path}: {exc}")

    return result


def _preserve_enabled_frontmatter(*, source_content: str, existing_content: str) -> str:
    """Carry a local workflow enabled flag forward when refreshing a seed template."""
    existing_frontmatter, _existing_body = parse_simple_frontmatter(
        existing_content, require_frontmatter=False
    )
    enabled = existing_frontmatter.get("enabled")
    if not isinstance(enabled, bool):
        return source_content
    return upsert_frontmatter_key(
        source_content,
        key="enabled",
        value="true" if enabled else "false",
    )


def _seed_vault_skills(vault_path: str) -> None:
    """Seed AssistantMD/Skills/ with example skill files if the directory is new."""
    skills_dir = Path(vault_path) / ASSISTANTMD_ROOT_DIR / SKILLS_DIR
    source_dir = SEED_TEMPLATE_DIR / "skills"
    if not source_dir.exists():
        return
    for seed_path in source_dir.iterdir():
        if not seed_path.is_file():
            continue
        target_path = skills_dir / seed_path.name
        if target_path.exists():
            continue
        try:
            shutil.copyfile(seed_path, target_path)
            logger.info(f"Seeded skill to {target_path}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Failed to seed skill {target_path}: {exc}")


# ---------------------------------------------------------------------------
# WorkflowLoader
# ---------------------------------------------------------------------------


class WorkflowLoader:
    """
    Manages loading and validation of vault-based workflow configurations.

    WARNING: Direct instantiation is discouraged. Use RuntimeContext.workflow_loader
    to ensure proper dependency injection and avoid multiple instances.
    """

    def __init__(
        self,
        _data_root: str | None = None,
        *,
        _allow_direct_instantiation: bool = False,
    ) -> None:
        if not _allow_direct_instantiation:
            raise RuntimeError(
                "Direct WorkflowLoader instantiation is discouraged. "
                "Use get_runtime_context().workflow_loader or bootstrap_runtime() instead."
            )
        self._data_root = _data_root or str(get_data_root())
        self._workflows: list[WorkflowDefinition] = []
        self._config_errors: list[ConfigurationError] = []
        self._last_loaded: datetime | None = None
        self._vault_info: dict[str, dict[str, Any]] = {}

    async def load_workflows(
        self,
        force_reload: bool = False,
        target_global_id: str | None = None,
    ) -> list[WorkflowDefinition]:
        """Load workflows from all vaults or a specific workflow."""
        target_vault = None
        target_name = None
        if target_global_id:
            if "/" not in target_global_id:
                raise ValueError(
                    f"Invalid target_global_id format. Expected 'vault/name', got: {target_global_id}"
                )
            target_vault, target_name = target_global_id.split("/", 1)

        vaults = discover_vaults(self._data_root)
        if target_vault:
            vaults = [target_vault] if target_vault in vaults else []
            if not vaults:
                raise ValueError(f"Target vault '{target_vault}' not found")

        if not vaults:
            if not target_global_id:
                self._workflows = []
                self._last_loaded = datetime.now()
            return []

        if not target_global_id:
            self._config_errors = []

        workflows: list[WorkflowDefinition] = []
        global_ids: set = set()
        vault_info: dict[str, Any] = {}
        system_workflow_templates = list_system_workflow_templates()

        for vault in vaults:
            vault_path = os.path.join(self._data_root, vault)
            workflow_files = discover_workflow_files(vault_path)
            loaded_names_for_vault: set[str] = set()

            vault_info[vault] = {
                "path": vault_path,
                "workflow_files": workflow_files,
                "workflows": [],
            }

            for file_path in workflow_files:
                name = None
                try:
                    resolved_name = _workflow_name_from_vault_file_path(
                        self._data_root, file_path
                    )
                    if resolved_name is None:
                        continue

                    vault, name = resolved_name

                    if target_name and name != target_name:
                        continue

                    sections = _parse_workflow_file(file_path)
                    raw_config = sections.get("__FRONTMATTER_CONFIG__", {})
                    if not raw_config:
                        raise ValueError("Missing YAML frontmatter configuration")

                    run_type = str(raw_config.get("run_type") or "").strip().lower()
                    if run_type == "context":
                        continue

                    validated_config = _validate_workflow_config(
                        raw_config, vault, name
                    )
                    workflow = load_workflow_from_file(
                        file_path, vault, name, validated_config, sections
                    )

                    if workflow.global_id in global_ids:
                        raise ValueError(
                            f"Duplicate workflow global ID: {workflow.global_id}"
                        )
                    global_ids.add(workflow.global_id)
                    workflows.append(workflow)
                    loaded_names_for_vault.add(workflow.name)

                    vault_info[vault]["workflows"].append(workflow.name)
                    logger.set_sinks(["validation"]).info(
                        "workflow_loaded",
                        data={
                            "vault": vault,
                            "workflow_id": workflow.global_id,
                            "workflow_path": file_path,
                            "enabled": workflow.enabled,
                            "schedule": workflow.schedule_string,
                            "run_type": workflow.run_type,
                        },
                    )

                except Exception as e:
                    config_error = ConfigurationError(
                        vault=vault,
                        workflow_name=name if "name" in locals() else None,
                        file_path=file_path,
                        error_message=str(e),
                        error_type=type(e).__name__,
                        timestamp=datetime.now(),
                    )
                    self._config_errors.append(config_error)
                    vault_identifier = (
                        f"{vault}/{name}" if "name" in locals() else vault
                    )
                    logger.add_sink("validation").error(
                        f"Failed to load workflow file {file_path}: {e}",
                        data={
                            "event": "workflow_load_failed",
                            "vault": vault,
                            "workflow_name": name if "name" in locals() else None,
                            "workflow_path": file_path,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "vault_identifier": vault_identifier,
                        },
                    )
                    continue

            for record in system_workflow_templates:
                system_template_name = (
                    record.name[:-3] if record.name.endswith(".md") else record.name
                )
                name = f"system/{system_template_name}"
                file_path = str(record.path or "")
                if not file_path:
                    continue
                if name in loaded_names_for_vault:
                    continue
                if target_name and name != target_name:
                    continue

                try:
                    sections = _parse_workflow_file(file_path)
                    raw_config = sections.get("__FRONTMATTER_CONFIG__", {})
                    if not raw_config:
                        raise ValueError("Missing YAML frontmatter configuration")

                    run_type = str(raw_config.get("run_type") or "").strip().lower()
                    if run_type == "context":
                        continue

                    validated_config = _validate_workflow_config(
                        raw_config, vault, name
                    )
                    workflow = load_workflow_from_file(
                        file_path, vault, name, validated_config, sections
                    )

                    if not workflow.enabled and not target_global_id:
                        continue

                    if workflow.global_id in global_ids:
                        raise ValueError(
                            f"Duplicate workflow global ID: {workflow.global_id}"
                        )
                    global_ids.add(workflow.global_id)
                    workflows.append(workflow)

                    logger.set_sinks(["validation"]).info(
                        "system_workflow_loaded",
                        data={
                            "vault": vault,
                            "workflow_id": workflow.global_id,
                            "workflow_path": file_path,
                            "enabled": workflow.enabled,
                            "schedule": workflow.schedule_string,
                            "run_type": workflow.run_type,
                            "system_template": system_template_name,
                        },
                    )

                except Exception as e:
                    config_error = ConfigurationError(
                        vault=vault,
                        workflow_name=name,
                        file_path=file_path,
                        error_message=str(e),
                        error_type=type(e).__name__,
                        timestamp=datetime.now(),
                    )
                    self._config_errors.append(config_error)
                    logger.add_sink("validation").error(
                        f"Failed to load system workflow template {file_path}: {e}",
                        data={
                            "event": "system_workflow_load_failed",
                            "vault": vault,
                            "workflow_name": name,
                            "workflow_path": file_path,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "vault_identifier": f"{vault}/{name}",
                        },
                    )
                    continue

        if target_global_id:
            if not workflows:
                raise ValueError(f"Target workflow '{target_global_id}' not found")
            self._workflows = [
                w for w in self._workflows if w.global_id != target_global_id
            ]
            self._workflows.append(workflows[0])
            return workflows
        else:
            self._workflows = workflows
            self._vault_info = vault_info
            self._last_loaded = datetime.now()
            return workflows

    def get_enabled_workflows(self) -> list[WorkflowDefinition]:
        return [w for w in self._workflows if w.enabled]

    def get_configuration_errors(self) -> list[ConfigurationError]:
        return self._config_errors.copy()

    def get_vault_info(self) -> dict[str, dict[str, Any]]:
        return self._vault_info.copy()

    def get_workflow_by_global_id(self, global_id: str) -> WorkflowDefinition | None:
        for w in self._workflows:
            if w.global_id == global_id:
                return w
        return None

    async def ensure_workflow_directories(self, workflow: WorkflowDefinition) -> None:
        os.makedirs(os.path.dirname(workflow.file_path), exist_ok=True)
