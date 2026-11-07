"""A copy cannot follow an alias retargeted while its file lock is pending."""

from __future__ import annotations

import asyncio
import hashlib
import sys

import pytest

from validation.support.application import Application
from validation.support.vault.vault_state import wait_for_event

pytestmark = pytest.mark.scenario


async def _wait_until_copy_is_waiting_for_file_lock() -> None:
    # Frame observation only coordinates the external filesystem change after
    # real path resolution and before lock acquisition. The behavioral oracle
    # is rejection plus preserved file bytes, not the application's stack shape.
    while True:
        for frame in sys._current_frames().values():
            names: set[str] = set()
            while frame is not None:
                names.add(frame.f_code.co_name)
                frame = frame.f_back
            if {
                "copy_vault_file_operation",
                "_exact_path_mutation_locks",
            } <= names:
                return
        await asyncio.sleep(0.01)


async def test_copy_rejects_destination_retargeted_while_waiting_for_file_lock(
    app: Application,
) -> None:
    from core.vault_state.file_mutations import (
        FileStateRestore,
        VaultMutationRejected,
        vault_file_mutation_lock,
    )
    from core.vault_state.file_operations import copy_vault_file_operation

    vault = app.vault("CopyLockIdentityVault")
    source_bytes = b"\x00\xffsource document"
    destination_bytes = b"\x00\xffreviewed destination"
    (vault / "source.data").write_bytes(source_bytes)
    for name in ("approved.data", "other.data"):
        (vault / name).write_bytes(destination_bytes)
    destination = vault / "destination.data"
    destination.symlink_to(vault / "approved.data")
    await app.start()
    await wait_for_event(
        app,
        0,
        name="vault_state_background_refresh_completed",
        expected={"reason": "startup"},
    )
    reviewed = (
        FileStateRestore(
            path="source.data",
            expected_exists=True,
            expected_sha256=hashlib.sha256(source_bytes).hexdigest(),
        ),
        FileStateRestore(
            path="approved.data",
            expected_exists=True,
            expected_sha256=hashlib.sha256(destination_bytes).hexdigest(),
        ),
    )
    copying = None
    try:
        with vault_file_mutation_lock(vault, vault / "approved.data"):
            copying = asyncio.create_task(
                asyncio.to_thread(
                    copy_vault_file_operation,
                    vault_path=vault,
                    path="source.data",
                    destination="destination.data",
                    overwrite=True,
                    reviewed_states=reviewed,
                )
            )
            await asyncio.wait_for(
                _wait_until_copy_is_waiting_for_file_lock(), timeout=5
            )
            destination.unlink()
            destination.symlink_to(vault / "other.data")
        with pytest.raises(VaultMutationRejected) as failure:
            await copying
        assert failure.value.code == "review_target_changed"
        assert (vault / "source.data").read_bytes() == source_bytes
        assert (vault / "approved.data").read_bytes() == destination_bytes
        assert (vault / "other.data").read_bytes() == destination_bytes
        app.evidence.save_json(
            "copy-lock-identity.json",
            {
                "error_type": failure.value.code,
                "source": (vault / "source.data").read_bytes().hex(),
                "approved_destination": (vault / "approved.data").read_bytes().hex(),
                "unreviewed_destination": (vault / "other.data").read_bytes().hex(),
            },
        )
    finally:
        # Drain the real worker even if synchronization or an assertion fails;
        # no filesystem work may outlive the isolated application fixture.
        if copying is not None:
            await asyncio.gather(copying, return_exceptions=True)
