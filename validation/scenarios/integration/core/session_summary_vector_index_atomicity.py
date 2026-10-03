"""Validate atomic replacement of session-summary field vectors."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.embeddings.test import TestEmbeddingModel

from core.memory.session_summary import (  # noqa: E402
    FIELD_VECTOR_NAMESPACE,
    FIELD_VECTOR_TABLE,
    SESSION_SUMMARY_FIELD_UNSET,
    SessionSummaryStore,
    session_summary_mutation_lock,
)
from core.settings.config_editor import upsert_model_mapping  # noqa: E402
from core.vector import VectorService  # noqa: E402
from validation.core.base_scenario import (  # noqa: E402
    BaseScenario,
    with_local_user_authority,
)


class SessionSummaryVectorIndexAtomicityScenario(BaseScenario):
    """Keep vector clearing and replacement consistent with durable summaries."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        controller = self._get_system_controller()
        system_root = str(controller._system_root)
        await self.start_system()
        store = SessionSummaryStore(system_root=system_root)
        vault_name = "SessionSummaryVectorAtomicityVault"
        session_id = "atomic-vector-session"

        fresh_delete_session_id = "fresh-summary-delete"
        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=fresh_delete_session_id,
            summary="Delete before any vector index exists",
        )
        with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
            conn.execute(f"DROP TABLE IF EXISTS {FIELD_VECTOR_TABLE}")
        self.soft_assert(
            store.delete_session_summary(
                vault_name=vault_name,
                session_id=fresh_delete_session_id,
            ),
            "Summary deletion should initialize and clear an absent vector table safely",
        )
        vector_service = _vector_service()

        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary="Original wetland restoration summary",
            domain="wetland restoration",
            work_product="funding proposal",
            user_intent="Prepare a wetland restoration funding proposal",
        )
        await store.index_session_summary_fields(
            vault_name=vault_name,
            session_id=session_id,
            vector_service=vector_service,
        )

        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary=None,
            domain=None,
            work_product=None,
            user_intent=None,
        )
        cleared_count = await store.index_session_summary_fields(
            vault_name=vault_name,
            session_id=session_id,
            vector_service=vector_service,
        )
        self.soft_assert_equal(
            cleared_count,
            0,
            "Clearing every vector field should report an empty index",
        )
        self.soft_assert_equal(
            _vector_rows(
                system_root=system_root,
                vault_name=vault_name,
                session_id=session_id,
            ),
            (),
            "Clearing every vector field should remove all prior vectors",
        )

        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary="Original wetland restoration summary",
            domain="wetland restoration",
            work_product="funding proposal",
            user_intent="Prepare a wetland restoration funding proposal",
        )
        await store.index_session_summary_fields(
            vault_name=vault_name,
            session_id=session_id,
            vector_service=vector_service,
        )
        original_rows = _vector_rows(
            system_root=system_root,
            vault_name=vault_name,
            session_id=session_id,
        )

        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary="Replacement forest conservation summary",
            domain="forest conservation",
            work_product="decision note",
            user_intent="Prepare a forest conservation decision note",
        )
        _install_mid_replacement_failure(
            system_root=system_root,
        )
        try:
            await store.index_session_summary_fields(
                vault_name=vault_name,
                session_id=session_id,
                vector_service=vector_service,
            )
        except sqlite3.IntegrityError:
            pass
        else:
            self.soft_assert(
                False,
                "An injected mid-replacement SQLite failure should propagate",
            )
        finally:
            _remove_mid_replacement_failure(system_root=system_root)

        self.soft_assert_equal(
            _vector_rows(
                system_root=system_root,
                vault_name=vault_name,
                session_id=session_id,
            ),
            original_rows,
            "A mid-replacement failure should preserve the exact prior vector index",
        )

        await self._assert_summary_writers_are_serialized(
            store=store,
            vault_name=vault_name,
        )
        await self._assert_cancelled_writers_rollback(
            store=store,
            vault_name=vault_name,
        )
        await self._assert_metadata_inheritance_waits_for_lock(
            store=store,
            vault_name=vault_name,
        )
        await self._assert_summary_delete_is_atomic(
            store=store,
            vector_service=vector_service,
            system_root=system_root,
            vault_name=vault_name,
        )

        self.teardown_scenario()
        self.assert_no_failures()

    async def _assert_summary_writers_are_serialized(
        self,
        *,
        store: SessionSummaryStore,
        vault_name: str,
    ) -> None:
        """Force overlapping writers and verify their index completions stay ordered."""
        import core.tools.session_ops as session_ops

        session_id = "serialized-summary-writers"
        from core.runtime.state import get_runtime_context

        get_runtime_context().chat_session_access.ensure_session(
            session_id,
            vault_name,
        )
        first_index_started = asyncio.Event()
        release_first_index = asyncio.Event()
        second_index_started = asyncio.Event()
        completed_index_values: list[str] = []

        async def controlled_index(
            controlled_store: SessionSummaryStore,
            *,
            vault_name: str,
            session_id: str,
        ) -> int:
            current = controlled_store.get_session_summary(
                vault_name=vault_name,
                session_id=session_id,
            )
            value = current.summary if current and current.summary else ""
            if value == "first concurrent summary":
                first_index_started.set()
                await release_first_index.wait()
            else:
                second_index_started.set()
            completed_index_values.append(value)
            return 1

        async def write(summary: str) -> dict[str, object]:
            return await session_ops._upsert_session_summary_operation(
                store=store,
                vault_name=vault_name,
                session_id=session_id,
                title=None,
                summary_data={"summary": summary},
                summary_metadata_input={},
                artifacts=(),
            )

        with patch.object(
            session_ops,
            "_index_session_summary_fields",
            side_effect=controlled_index,
        ):
            first = asyncio.create_task(write("first concurrent summary"))
            await first_index_started.wait()
            second = asyncio.create_task(write("second concurrent summary"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.soft_assert(
                not second_index_started.is_set(),
                "A second summary writer should wait until the first index refresh completes",
            )
            release_first_index.set()
            await asyncio.gather(first, second)

        final_summary = store.get_session_summary(
            vault_name=vault_name,
            session_id=session_id,
        )
        self.soft_assert_equal(
            completed_index_values,
            ["first concurrent summary", "second concurrent summary"],
            "Serialized writers should complete index refreshes in mutation order",
        )
        self.soft_assert_equal(
            completed_index_values[-1],
            final_summary.summary if final_summary else None,
            "The final summary row and last completed vector refresh should agree",
        )

        deleted_session_id = "deleted-before-writer-lock"
        runtime = get_runtime_context()
        runtime.chat_session_access.ensure_session(deleted_session_id, vault_name)
        async with session_summary_mutation_lock(
            vault_name=vault_name,
            session_id=deleted_session_id,
        ):
            delayed_writer = asyncio.create_task(
                session_ops._upsert_session_summary_operation(
                    store=store,
                    vault_name=vault_name,
                    session_id=deleted_session_id,
                    title=None,
                    summary_data={"summary": "must not become orphaned"},
                    summary_metadata_input={},
                    artifacts=(),
                )
            )
            await asyncio.sleep(0)
            runtime.chat_store.delete_sessions(
                vault_name,
                session_id=deleted_session_id,
            )
            store.delete_session_summary(
                vault_name=vault_name,
                session_id=deleted_session_id,
            )
        try:
            await delayed_writer
        except LookupError:
            pass
        else:
            self.soft_assert(
                False,
                "A writer queued before session deletion should fail closed after acquiring the lock",
            )
        self.soft_assert(
            store.get_session_summary(
                vault_name=vault_name,
                session_id=deleted_session_id,
            )
            is None,
            "A queued writer must not recreate an orphan summary after session deletion",
        )

    async def _assert_cancelled_writers_rollback(
        self,
        *,
        store: SessionSummaryStore,
        vault_name: str,
    ) -> None:
        """A cancellation must restore durable summary and FTS state before propagating."""
        import api.services.chat_sessions as chat_sessions_service
        import core.tools.session_ops as session_ops
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()

        tool_session_id = "cancelled-tool-summary-writer"
        runtime.chat_session_access.ensure_session(tool_session_id, vault_name)
        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=tool_session_id,
            summary="tool summary before cancellation",
            metadata={"durable": "tool"},
        )
        with patch.object(
            session_ops,
            "_index_session_summary_fields",
            side_effect=asyncio.CancelledError,
        ):
            try:
                await session_ops._upsert_session_summary_operation(
                    store=store,
                    vault_name=vault_name,
                    session_id=tool_session_id,
                    title="title that must rollback",
                    summary_data={"summary": "tool summary that must rollback"},
                    summary_metadata_input=SESSION_SUMMARY_FIELD_UNSET,
                    artifacts=(),
                )
            except asyncio.CancelledError:
                pass
            else:
                self.soft_assert(False, "Tool-writer cancellation should propagate")
        tool_summary = store.get_session_summary(
            vault_name=vault_name,
            session_id=tool_session_id,
        )
        self.soft_assert_equal(
            tool_summary.summary if tool_summary else None,
            "tool summary before cancellation",
            "Tool-writer cancellation should restore the previous summary row",
        )
        self.soft_assert_equal(
            tool_summary.title if tool_summary else None,
            None,
            "Tool-writer cancellation should restore a previously null title",
        )
        self.soft_assert_equal(
            _fts_summary(
                system_root=store.system_root,
                vault_name=vault_name,
                session_id=tool_session_id,
            ),
            "tool summary before cancellation",
            "Tool-writer cancellation should restore the previous FTS row",
        )

        api_session_id = "cancelled-api-summary-writer"
        runtime.chat_session_access.ensure_session(api_session_id, vault_name)
        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=api_session_id,
            summary="api summary before cancellation",
            metadata={"durable": "api"},
        )
        with patch.object(
            chat_sessions_service,
            "_index_session_summary_for_api",
            side_effect=asyncio.CancelledError,
        ):
            try:
                await chat_sessions_service.update_chat_session_summary(
                    vault_name=vault_name,
                    session_id=api_session_id,
                    data={"summary": "api summary that must rollback"},
                )
            except asyncio.CancelledError:
                pass
            else:
                self.soft_assert(False, "API-writer cancellation should propagate")
        api_summary = store.get_session_summary(
            vault_name=vault_name,
            session_id=api_session_id,
        )
        self.soft_assert_equal(
            api_summary.summary if api_summary else None,
            "api summary before cancellation",
            "API-writer cancellation should restore the previous summary row",
        )
        self.soft_assert_equal(
            _fts_summary(
                system_root=store.system_root,
                vault_name=vault_name,
                session_id=api_session_id,
            ),
            "api summary before cancellation",
            "API-writer cancellation should restore the previous FTS row",
        )

    async def _assert_metadata_inheritance_waits_for_lock(
        self,
        *,
        store: SessionSummaryStore,
        vault_name: str,
    ) -> None:
        """Do not read inherited metadata until the writer owns the session lock."""
        import core.tools.session_ops as session_ops
        from core.runtime.state import get_runtime_context

        session_id = "metadata-inside-summary-lock"
        get_runtime_context().chat_session_access.ensure_session(session_id, vault_name)
        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary="existing summary",
            metadata={"generation": "existing"},
        )
        metadata_read = asyncio.Event()
        original_metadata_helper = session_ops._with_current_history_metadata

        def observe_metadata_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
            metadata_read.set()
            return original_metadata_helper(*args, **kwargs)

        async def skip_index(*args: object, **kwargs: object) -> int:
            return 0

        with (
            patch.object(
                session_ops,
                "_with_current_history_metadata",
                side_effect=observe_metadata_read,
            ),
            patch.object(
                session_ops,
                "_index_session_summary_fields",
                side_effect=skip_index,
            ),
        ):
            async with session_summary_mutation_lock(
                vault_name=vault_name,
                session_id=session_id,
            ):
                writer = asyncio.create_task(
                    session_ops._upsert_session_summary_operation(
                        store=store,
                        vault_name=vault_name,
                        session_id=session_id,
                        title=None,
                        summary_data={"summary": "updated summary"},
                        summary_metadata_input=SESSION_SUMMARY_FIELD_UNSET,
                        artifacts=(),
                    )
                )
                await asyncio.sleep(0)
                self.soft_assert(
                    not metadata_read.is_set(),
                    "Inherited metadata should not be read before acquiring the session lock",
                )
            await writer
        self.soft_assert(
            metadata_read.is_set(),
            "Inherited metadata should be read after acquiring the session lock",
        )

    async def _assert_summary_delete_is_atomic(
        self,
        *,
        store: SessionSummaryStore,
        vector_service: VectorService,
        system_root: str,
        vault_name: str,
    ) -> None:
        """A failure deleting vectors must preserve the row, FTS entry, and vectors."""
        session_id = "atomic-summary-delete"
        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary="summary preserved on failed delete",
            domain="atomic lifecycle",
        )
        await store.index_session_summary_fields(
            vault_name=vault_name,
            session_id=session_id,
            vector_service=vector_service,
        )
        original_vectors = _vector_rows(
            system_root=system_root,
            vault_name=vault_name,
            session_id=session_id,
        )
        _install_delete_failure(system_root=system_root, session_id=session_id)
        try:
            store.delete_session_summary(
                vault_name=vault_name,
                session_id=session_id,
            )
        except sqlite3.IntegrityError:
            pass
        else:
            self.soft_assert(False, "Injected vector-delete failure should propagate")
        finally:
            _remove_delete_failure(system_root=system_root)

        self.soft_assert(
            store.get_session_summary(vault_name=vault_name, session_id=session_id)
            is not None,
            "Failed deletion should preserve the summary row",
        )
        self.soft_assert_equal(
            _fts_summary(
                system_root=system_root,
                vault_name=vault_name,
                session_id=session_id,
            ),
            "summary preserved on failed delete",
            "Failed deletion should preserve the FTS row",
        )
        self.soft_assert_equal(
            _vector_rows(
                system_root=system_root,
                vault_name=vault_name,
                session_id=session_id,
            ),
            original_vectors,
            "Failed deletion should preserve all summary vectors",
        )

        self.soft_assert(
            store.delete_session_summary(vault_name=vault_name, session_id=session_id),
            "Summary deletion should succeed after failure injection is removed",
        )
        self.soft_assert_equal(
            _vector_rows(
                system_root=system_root,
                vault_name=vault_name,
                session_id=session_id,
            ),
            (),
            "Successful deletion should remove summary vectors",
        )

        await self._assert_chat_delete_preserves_canonical_on_summary_failure(
            store=store,
            vector_service=vector_service,
            system_root=system_root,
            vault_name=vault_name,
        )

    async def _assert_chat_delete_preserves_canonical_on_summary_failure(
        self,
        *,
        store: SessionSummaryStore,
        vector_service: VectorService,
        system_root: str,
        vault_name: str,
    ) -> None:
        """Derived-memory cleanup must fail before canonical chat deletion."""
        import api.services.chat_sessions as chat_sessions_service
        from core.runtime.state import get_runtime_context

        session_id = "summary-failure-before-chat-delete"
        runtime = get_runtime_context()
        runtime.chat_session_access.ensure_session(session_id, vault_name)
        store.upsert_session_summary(
            vault_name=vault_name,
            session_id=session_id,
            summary="summary whose deletion will fail",
        )
        await store.index_session_summary_fields(
            vault_name=vault_name,
            session_id=session_id,
            vector_service=vector_service,
        )
        _install_delete_failure(system_root=system_root, session_id=session_id)
        try:
            await chat_sessions_service.delete_chat_session(vault_name, session_id)
        except sqlite3.IntegrityError:
            pass
        else:
            self.soft_assert(
                False, "Summary cleanup failure should abort chat deletion"
            )
        finally:
            _remove_delete_failure(system_root=system_root)

        self.soft_assert(
            runtime.chat_store.get_session(session_id, vault_name) is not None,
            "Canonical chat should remain when derived summary cleanup fails",
        )
        self.soft_assert(
            store.get_session_summary(vault_name=vault_name, session_id=session_id)
            is not None,
            "Failed derived cleanup should preserve the summary transaction",
        )
        await chat_sessions_service.delete_chat_session(vault_name, session_id)


def _vector_service() -> VectorService:
    upsert_model_mapping(
        name="embeddings",
        provider="openai",
        model_string="text-embedding-3-small",
        capabilities=["embedding"],
        dimensions=8,
        description="Validation embedding model alias",
    )
    return VectorService(
        embedding_model_overrides={
            "embeddings": TestEmbeddingModel(
                model_name="atomic-index-test",
                provider_name="test",
                dimensions=8,
            )
        }
    )


def _vector_rows(
    *,
    system_root: str,
    vault_name: str,
    session_id: str,
) -> tuple[tuple[object, ...], ...]:
    item_prefix = f"{vault_name}:{session_id}:%"
    with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
        rows = conn.execute(
            f"""
            SELECT item_id, input_text, input_fingerprint, embedding_space_id,
                   dimensions, model_alias, provider_name, model_name,
                   vector_json, metadata_json, created_at, updated_at
            FROM {FIELD_VECTOR_TABLE}
            WHERE namespace = ? AND item_id LIKE ?
            ORDER BY item_id, embedding_space_id
            """,
            (FIELD_VECTOR_NAMESPACE, item_prefix),
        ).fetchall()
    return tuple(tuple(row) for row in rows)


def _install_mid_replacement_failure(*, system_root: str) -> None:
    with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
        conn.execute(
            f"""
            CREATE TRIGGER validation_fail_session_summary_vector_replace
            BEFORE INSERT ON {FIELD_VECTOR_TABLE}
            WHEN NEW.item_id LIKE '%:domain'
            BEGIN
                SELECT RAISE(ABORT, 'injected vector replacement failure');
            END
            """
        )


def _remove_mid_replacement_failure(*, system_root: str) -> None:
    with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
        conn.execute(
            "DROP TRIGGER IF EXISTS validation_fail_session_summary_vector_replace"
        )


def _fts_summary(
    *,
    system_root: str | None,
    vault_name: str,
    session_id: str,
) -> str | None:
    if system_root is None:
        raise ValueError("The scenario requires an explicit system root")
    with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
        row = conn.execute(
            """
            SELECT summary
            FROM session_summaries_fts
            WHERE vault_name = ? AND session_id = ?
            """,
            (vault_name, session_id),
        ).fetchone()
    return str(row[0]) if row else None


def _install_delete_failure(*, system_root: str, session_id: str) -> None:
    item_prefix = f"%:{session_id}:%"
    with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
        conn.execute(
            f"""
            CREATE TRIGGER validation_fail_session_summary_vector_delete
            BEFORE DELETE ON {FIELD_VECTOR_TABLE}
            WHEN OLD.item_id LIKE '{item_prefix}'
            BEGIN
                SELECT RAISE(ABORT, 'injected vector deletion failure');
            END
            """
        )


def _remove_delete_failure(*, system_root: str) -> None:
    with sqlite3.connect(Path(system_root) / "session_summaries.db") as conn:
        conn.execute(
            "DROP TRIGGER IF EXISTS validation_fail_session_summary_vector_delete"
        )
