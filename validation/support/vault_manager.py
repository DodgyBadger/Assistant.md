"""
Vault management for V2 validation scenarios.

Provides high-level vault operations while maintaining evidence collection.
"""

import shutil
from pathlib import Path

from core.constants import ASSISTANTMD_ROOT_DIR, AUTHORING_DIR, CHAT_SESSIONS_DIR
from core.logger import UnifiedLogger

from .paths import resolve_validation_app_root


class VaultManager:
    """Manages vault creation and file operations for validation scenarios."""

    def __init__(self, run_path: Path):
        self.run_path = run_path
        self.vaults_root = run_path / "test_vaults"
        self.vaults_root.mkdir(exist_ok=True)
        self.logger = UnifiedLogger(tag="vault-manager")

        # Track created vaults for security
        self.created_vaults: dict[str, Path] = {}
        self.app_root = resolve_validation_app_root()

    def create_vault(self, name: str) -> Path:
        """Create minimal empty vault structure."""
        vault_path = self.vaults_root / name
        vault_path.mkdir(parents=True, exist_ok=True)

        # Create basic directory structure
        workflows_dir = vault_path / ASSISTANTMD_ROOT_DIR / AUTHORING_DIR
        chat_sessions_dir = vault_path / ASSISTANTMD_ROOT_DIR / CHAT_SESSIONS_DIR
        workflows_dir.mkdir(parents=True, exist_ok=True)
        chat_sessions_dir.mkdir(parents=True, exist_ok=True)

        self.created_vaults[name] = vault_path
        return vault_path

    def copy_files(
        self,
        source_path: str,
        vault: Path,
        dest_dir: str = "",
        dest_filename: str = None,
    ):
        """Copy files/directories from source to vault.

        Args:
            source_path: Path relative to the configured application root
                (e.g., 'validation/fixtures/files/test_image.jpg')
            vault: Target vault (must be a vault created by this manager)
            dest_dir: Optional subdirectory within vault (e.g., 'AssistantMD/Workflows')
            dest_filename: Optional filename to rename single file (allows overwriting)
        """
        # Security: Ensure vault is one we created
        if vault not in self.created_vaults.values():
            raise ValueError(
                f"Vault {vault} not created by this scenario - security violation"
            )

        # Resolve source relative to app root
        source_full_path = self.app_root / source_path
        if not source_full_path.exists():
            raise FileNotFoundError(f"Source path not found: {source_full_path}")

        # Determine destination
        dest_path = vault / dest_dir if dest_dir else vault
        dest_path.mkdir(parents=True, exist_ok=True)

        # Copy single file or entire directory
        if source_full_path.is_file():
            # Use custom filename if provided, otherwise use original name
            filename = dest_filename if dest_filename else source_full_path.name
            dest_file = dest_path / filename
            shutil.copy2(source_full_path, dest_file)
        elif source_full_path.is_dir():
            # Directory copy doesn't support renaming
            if dest_filename:
                raise ValueError("dest_filename not supported for directory copy")
            for item in source_full_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest_path / item.name)
                elif item.is_dir():
                    shutil.copytree(item, dest_path / item.name, dirs_exist_ok=True)

    def create_file(self, vault: Path, file_path: str, content: str):
        """Create single file with content in vault.

        Args:
            vault: Target vault (must be a vault created by this manager)
            file_path: Path within vault (e.g., 'AssistantMD/Workflows/my_workflow.md')
            content: File content (can be large block of text)
        """
        # Security: Ensure vault is one we created
        if vault not in self.created_vaults.values():
            raise ValueError(
                f"Vault {vault} not created by this scenario - security violation"
            )

        full_path = vault / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

    # Removed old methods - using generic copy_files and create_file
