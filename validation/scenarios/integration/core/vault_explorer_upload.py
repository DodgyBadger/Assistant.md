"""Validate create-only binary uploads through the Vault Explorer API."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class VaultExplorerUploadScenario(BaseScenario):
    """Validate upload boundaries, bytes, and mutation attribution."""

    async def test_scenario(self):
        vault = self.create_vault("VaultExplorerUploadVault")
        await self.start_system()
        client = self._get_api_client()._client  # noqa: SLF001

        payload = b"%PDF-1.7\n\x00binary-upload\n%%EOF"
        response = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "AssistantMD/Import/source.pdf"},
            files={"file": ("source.pdf", payload, "application/pdf")},
        )
        self.soft_assert_equal(
            response.status_code, 200, "Binary upload should succeed"
        )
        self.soft_assert_equal(
            (vault / "AssistantMD/Import/source.pdf").read_bytes(),
            payload,
            "Upload should preserve binary bytes",
        )

        activity = self.call_api(f"/api/vaults/{vault.name}/activity")
        groups = activity.json().get("groups", [])
        upload_group = next(
            (
                group
                for group in groups
                if group.get("activity_label") == "Upload AssistantMD/Import/source.pdf"
            ),
            None,
        )
        self.soft_assert(
            upload_group is not None, "Upload should create Explorer activity"
        )
        mutations = upload_group.get("mutations", []) if upload_group else []
        self.soft_assert(
            any(
                mutation.get("path") == "AssistantMD/Import/source.pdf"
                and mutation.get("operation") == "write"
                and mutation.get("after_exists") is True
                for mutation in mutations
            ),
            "Upload should be represented by a recorded vault write",
        )

        collision = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "AssistantMD/Import/source.pdf"},
            files={"file": ("source.pdf", b"replacement", "application/pdf")},
        )
        self.soft_assert_equal(
            collision.status_code,
            409,
            "Upload should reject an existing destination",
        )
        self.soft_assert_equal(
            (vault / "AssistantMD/Import/source.pdf").read_bytes(),
            payload,
            "Rejected collision should preserve existing bytes",
        )

        update_limit = self.call_api(
            "/api/system/settings/general/vault_upload_max_mb_per_file",
            method="PUT",
            data={"value": "1"},
        )
        self.soft_assert_equal(
            update_limit.status_code,
            200,
            "Vault upload limit setting should update without restart",
        )
        oversized = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "oversized.pdf"},
            files={
                "file": (
                    "oversized.pdf",
                    b"x" * ((1024 * 1024) + 1),
                    "application/pdf",
                )
            },
        )
        self.soft_assert_equal(
            oversized.status_code,
            413,
            "Oversized upload should be rejected",
        )
        self.soft_assert(
            not (vault / "oversized.pdf").exists(),
            "Oversized upload should not create a destination",
        )

        partial_path = vault / "partial-write.pdf"
        partial_path_resolved = partial_path.resolve()

        class FailingBinaryDestination:
            def __enter__(self):
                self._stream = partial_path.open("wb")
                return self

            def __exit__(self, exc_type, exc, traceback):
                self._stream.close()

            def write(self, content):
                self._stream.write(content[:4])
                raise OSError("simulated interrupted upload write")

        original_io_open = io.open

        def fail_partial_upload(path, mode="r", *args, **kwargs):
            if Path(path).resolve() == partial_path_resolved and mode == "xb":
                return FailingBinaryDestination()
            return original_io_open(path, mode, *args, **kwargs)

        with patch.object(io, "open", fail_partial_upload):
            interrupted = client.post(
                f"/api/vaults/{vault.name}/files/upload",
                params={"path": partial_path.name},
                files={
                    "file": (
                        partial_path.name,
                        payload,
                        "application/pdf",
                    )
                },
            )
        self.soft_assert_equal(
            interrupted.status_code,
            500,
            "Interrupted upload writes should report a server failure",
        )
        self.soft_assert(
            not partial_path.exists(),
            "Interrupted upload writes should remove the partial destination",
        )

        traversal = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "../outside.pdf"},
            files={"file": ("outside.pdf", payload, "application/pdf")},
        )
        self.soft_assert_equal(
            traversal.status_code,
            400,
            "Traversal destination should be rejected",
        )
        self.soft_assert(
            not (vault.parent / "outside.pdf").exists(),
            "Traversal upload should remain inside the vault boundary",
        )

        for unsafe_path in (
            "/absolute.pdf",
            ".",
            "folder/./dot.pdf",
            r"C:\outside.pdf",
            "control\x00.pdf",
        ):
            unsafe = client.post(
                f"/api/vaults/{vault.name}/files/upload",
                params={"path": unsafe_path},
                files={"file": ("source.pdf", payload, "application/pdf")},
            )
            self.soft_assert_equal(
                unsafe.status_code,
                400,
                f"Unsafe upload path should be rejected: {unsafe_path!r}",
            )

        outside_directory = vault.parent / "upload-symlink-target"
        outside_directory.mkdir()
        (vault / "escape-link").symlink_to(outside_directory, target_is_directory=True)
        symlink_escape = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "escape-link/escaped.pdf"},
            files={"file": ("escaped.pdf", payload, "application/pdf")},
        )
        self.soft_assert_equal(
            symlink_escape.status_code,
            400,
            "Upload through an escaping directory symlink should be rejected",
        )
        self.soft_assert(
            not (outside_directory / "escaped.pdf").exists(),
            "Symlink escape should not write outside the selected vault",
        )

        extra_part = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "extra-part.pdf"},
            files=[
                ("file", ("extra-part.pdf", payload, "application/pdf")),
                ("unexpected", ("second.pdf", payload, "application/pdf")),
            ],
        )
        self.soft_assert_equal(
            extra_part.status_code,
            400,
            "Upload should reject unrelated multipart file parts",
        )
        self.soft_assert(
            not (vault / "extra-part.pdf").exists(),
            "Rejected multipart request should not create a destination",
        )

        untrusted_filename = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "safe-name.pdf"},
            files={
                "file": (
                    "../../server.py",
                    payload,
                    "application/octet-stream",
                )
            },
        )
        self.soft_assert_equal(
            untrusted_filename.status_code,
            200,
            "Multipart filename should not control the vault destination",
        )
        self.soft_assert_equal(
            (vault / "safe-name.pdf").read_bytes(),
            payload,
            "Only the validated path parameter should select the destination",
        )
        self.soft_assert(
            not (vault.parent / "server.py").exists(),
            "Untrusted multipart filename should not escape the vault",
        )

        disable_uploads = self.call_api(
            "/api/system/settings/general/vault_upload_max_mb_per_file",
            method="PUT",
            data={"value": "0"},
        )
        self.soft_assert_equal(
            disable_uploads.status_code,
            200,
            "Vault uploads should support an explicit disabled setting",
        )
        disabled = client.post(
            f"/api/vaults/{vault.name}/files/upload",
            params={"path": "disabled.pdf"},
            files={"file": ("disabled.pdf", payload, "application/pdf")},
        )
        self.soft_assert_equal(
            disabled.status_code,
            403,
            "Disabled Vault Explorer uploads should be rejected",
        )
        self.soft_assert(
            not (vault / "disabled.pdf").exists(),
            "Disabled upload should not create a destination",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
