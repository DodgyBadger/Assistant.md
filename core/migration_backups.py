"""Owned paths and organization for migration rollback copies."""

from __future__ import annotations

from pathlib import Path

MIGRATION_BACKUP_DIRECTORY = "migration_backups"
ROOT_BACKUP_FILENAMES = frozenset({"secrets.yaml.bak", "settings.yaml.bak"})


def get_migration_backup_directory(system_root: str | Path) -> Path:
    """Return the migration backup directory without creating it."""
    return Path(system_root) / MIGRATION_BACKUP_DIRECTORY


def next_available_backup_path(path: Path) -> Path:
    """Return the base backup path or its next available numbered variant."""
    if not path.exists():
        return path
    version = 2
    while True:
        candidate = path.with_name(f"{path.name} ({version})")
        if not candidate.exists():
            return candidate
        version += 1


def prepare_migration_backup_path(system_root: str | Path, filename: str) -> Path:
    """Create the owned backup directory and choose a non-conflicting path."""
    backup_directory = get_migration_backup_directory(system_root)
    backup_directory.mkdir(parents=True, exist_ok=True)
    return next_available_backup_path(backup_directory / filename)


def organize_legacy_migration_backups(system_root: str | Path) -> int:
    """Move recognized root-level migration backups into their owned directory."""
    root = Path(system_root)
    legacy_paths = sorted(
        {
            *root.glob("*.db.backup-*"),
            *(root / name for name in ROOT_BACKUP_FILENAMES if (root / name).exists()),
        }
    )
    if not legacy_paths:
        return 0
    for source in legacy_paths:
        destination = prepare_migration_backup_path(root, source.name)
        source.rename(destination)
    return len(legacy_paths)
