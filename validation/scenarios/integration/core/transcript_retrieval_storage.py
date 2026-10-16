"""Validate canonical transcript indexing and authorized retrieval."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.chat_store import ChatStore  # noqa: E402
from core.chat.schema import ensure_chat_sessions_schema  # noqa: E402
from core.chat.session_access import ChatSessionAccessService  # noqa: E402
from core.chat.transcript_retrieval import (  # noqa: E402
    MAX_SEARCH_LIMIT,
    TranscriptRetrievalService,
)
from core.identity import (  # noqa: E402
    LOCAL_USER_PRINCIPAL_ID,
    AuthorizationService,
    ExecutionAuthority,
    use_execution_authority,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402


class TranscriptRetrievalStorageScenario(BaseScenario):
    """Prove the transcript index lifecycle below the model-facing tool."""

    async def test_scenario(self) -> None:
        system_root = self.artifacts_dir / "system"
        system_root.mkdir()
        store = ChatStore(str(system_root))
        owner = ExecutionAuthority(LOCAL_USER_PRINCIPAL_ID)
        attacker = ExecutionAuthority("transcript-attacker")
        vault_name = "TranscriptVault"
        other_vault = "OtherTranscriptVault"
        session_id = "transcript-primary"
        other_session_id = "transcript-other"

        store.ensure_session(
            session_id,
            vault_name,
            owner_principal_id=owner.principal_id,
        )
        store.add_messages(
            session_id,
            vault_name,
            [
                _message("Opening context without the target phrase."),
                _message("The cobalt-lantern decision belongs to the primary vault."),
                _message("Closing context after the target phrase."),
                _tool_result(
                    "source_probe",
                    {"status": "failed", "error": "granite-signal primary failure"},
                    "source-probe-call",
                ),
                _tool_result(
                    "session_ops",
                    '{"operation":"search_transcript","matches":[{"excerpt":"granite-signal primary failure"}]}',
                    "retrieval-call",
                ),
            ],
        )
        store.ensure_session(
            other_session_id,
            other_vault,
            owner_principal_id=owner.principal_id,
        )
        store.add_messages(
            other_session_id,
            other_vault,
            [_message("The cobalt-lantern phrase also exists in another vault.")],
        )

        ensure_chat_sessions_schema(str(system_root), apply_migrations=True)
        access = ChatSessionAccessService(store, AuthorizationService())
        retrieval = TranscriptRetrievalService(store, access, excerpt_chars=80)

        with use_execution_authority(owner):
            backfilled = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="cobalt lantern",
            )
            self.soft_assert_equal(
                [
                    (hit.anchor.session_id, hit.anchor.sequence_index)
                    for hit in backfilled
                ],
                [(session_id, 1)],
                "Migration should backfill existing canonical messages with stable anchors",
            )
            self.soft_assert(
                all(hit.vault_name == vault_name for hit in backfilled),
                "Search SQL should scope matches to the requested vault before returning hits",
            )
            self.soft_assert(
                all(len(hit.excerpt) <= 80 for hit in backfilled),
                "Search excerpts should stay inside the configured character bound",
            )

            structured = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="granite signal primary failure",
            )
            self.soft_assert_equal(
                structured[0].anchor.sequence_index if structured else None,
                3,
                "Structured primary tool evidence should be the highest-ranked source",
            )
            self.soft_assert(
                all(hit.anchor.sequence_index != 4 for hit in structured),
                "A later retrieval envelope quoting the evidence should not be a search candidate",
            )
            self.soft_assert_equal(
                structured[0].source_kind if structured else None,
                "tool_result",
                "Search hits should distinguish direct tool evidence from user-authored text",
            )
            self.soft_assert_equal(
                structured[0].tool_names if structured else None,
                ("source_probe",),
                "Search hits should identify the tool that produced direct evidence",
            )
            structured_window = retrieval.get_window(
                vault_name=vault_name,
                session_id=session_id,
                sequence_index=3,
                before=0,
                after=0,
            )
            self.soft_assert(
                "granite-signal primary failure"
                in structured_window.messages[0].content,
                "Transcript windows should render structured tool evidence readably",
            )
            self.soft_assert_equal(
                structured_window.messages[0].source_kind,
                "tool_result",
                "Transcript windows should retain source provenance",
            )

            store.add_messages(
                session_id,
                vault_name,
                [_message("A newly indexed zephyr-compass fact." * 20)],
            )
            immediate = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="zephyr compass",
            )
            self.soft_assert_equal(
                [hit.anchor.sequence_index for hit in immediate],
                [5],
                "New canonical writes should become searchable in the same transaction",
            )
            self.soft_assert(
                bool(immediate) and len(immediate[0].excerpt) <= 80,
                "Long matching messages should return bounded excerpts",
            )

            store.add_messages(
                session_id,
                vault_name,
                [
                    _message("The café résumé archive is stored in Montréal."),
                    _message("東京 京都の旅程はこのメッセージに記録されています。"),
                ],
            )
            accented = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="café résumé",
            )
            self.soft_assert_equal(
                [hit.anchor.sequence_index for hit in accented],
                [6],
                "Unicode-aware query tokenization should find accented transcript text",
            )
            non_latin = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="東京 京都",
            )
            self.soft_assert_equal(
                [hit.anchor.sequence_index for hit in non_latin],
                [7],
                "Unicode-aware query tokenization should find non-Latin transcript text",
            )

            exact_range = retrieval.get_range(
                vault_name=vault_name,
                session_id=session_id,
                after_sequence_index=0,
                through_sequence_index=2,
            )
            self.soft_assert_equal(
                [message.sequence_index for message in exact_range],
                [1, 2],
                "Canonical interval reads should preserve exact chronological bounds",
            )

            db_path = system_root / "chat_sessions.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE chat_messages
                    SET content_text = ?
                    WHERE session_id = ? AND vault_name = ? AND sequence_index = 1
                    """,
                    (
                        "The amber-orbit decision replaced the earlier wording.",
                        session_id,
                        vault_name,
                    ),
                )
                conn.commit()
            self.soft_assert_equal(
                retrieval.search(
                    vault_name=vault_name,
                    session_id=session_id,
                    query="cobalt lantern",
                ),
                [],
                "Updating canonical text should remove stale index terms",
            )
            updated = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="amber orbit",
            )
            self.soft_assert_equal(
                [hit.anchor.sequence_index for hit in updated],
                [1],
                "Updating canonical text should index replacement terms",
            )

            malformed = retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query='" OR * NOT ( amber',
            )
            self.soft_assert(
                isinstance(malformed, list),
                "Malformed FTS-shaped input should be normalized instead of leaking SQL errors",
            )
            self._assert_value_error(
                lambda: retrieval.search(
                    vault_name=vault_name,
                    session_id=session_id,
                    query="!",
                ),
                "Queries without searchable terms should fail clearly",
            )
            self._assert_value_error(
                lambda: retrieval.search(
                    vault_name=vault_name,
                    session_id=session_id,
                    query="amber",
                    limit=MAX_SEARCH_LIMIT + 1,
                ),
                "Search should enforce its hard result bound",
            )

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO chat_messages_fts(chat_messages_fts) VALUES ('delete-all')"
                )
                conn.commit()
            self.soft_assert_equal(
                retrieval.search(
                    vault_name=vault_name,
                    session_id=session_id,
                    query="amber orbit",
                ),
                [],
                "The test should be able to simulate a missing derived index",
            )
            retrieval.rebuild_index()
            self.soft_assert_equal(
                [
                    hit.anchor.sequence_index
                    for hit in retrieval.search(
                        vault_name=vault_name,
                        session_id=session_id,
                        query="amber orbit",
                    )
                ],
                [1],
                "Index rebuild should deterministically restore canonical matches",
            )

            self._assert_lookup_error(
                lambda: retrieval.search(
                    vault_name=other_vault,
                    session_id=session_id,
                    query="amber",
                ),
                "A mismatched vault should be concealed as a missing session",
            )

        # One long session must not exhaust the cross-session candidate budget.
        for identity, count, principal in (
            ("aaa-long", 105, owner.principal_id),
            ("zzz-short", 1, owner.principal_id),
            ("hidden-match", 105, attacker.principal_id),
        ):
            store.ensure_session(identity, vault_name, owner_principal_id=principal)
            store.add_messages(
                identity, vault_name, [_message("wetland") for _ in range(count)]
            )
        with use_execution_authority(owner):
            balanced = retrieval.search_vault(
                vault_name=vault_name, query="wetland", limit=2
            )
            assert [hit.anchor.session_id for hit in balanced] == [
                "aaa-long",
                "zzz-short",
            ]
            assert all(hit.anchor.sequence_index == 0 for hit in balanced)
            assert [
                hit.anchor.session_id
                for hit in retrieval.search_vault(
                    vault_name=vault_name,
                    query="wetland",
                    limit=2,
                    session_ids={"zzz-short"},
                )
            ] == ["zzz-short"]
            # Mixed retrieval echoes can be SQL matches without matching primary
            # content. Later candidates from that session must remain available.
            store.add_messages(
                "aaa-long",
                vault_name,
                [
                    ModelRequest(
                        parts=[
                            UserPromptPart(content="unrelated primary text"),
                            ToolReturnPart(
                                tool_name="session_ops",
                                tool_call_id="echo",
                                content="fallbackneedle " * 20,
                            ),
                        ]
                    ),
                    _message("fallbackneedle"),
                ],
            )
            fallback = retrieval.search_vault(
                vault_name=vault_name, query="fallbackneedle", limit=2
            )
            assert len(fallback) == 1 and fallback[0].anchor.sequence_index == 106

        with use_execution_authority(attacker):
            self._assert_lookup_error(
                lambda: retrieval.search(
                    vault_name=vault_name,
                    session_id=session_id,
                    query="amber",
                ),
                "A foreign principal should not retrieve an owned transcript",
            )

        store.delete_sessions(vault_name, session_id=session_id)
        with sqlite3.connect(system_root / "chat_sessions.db") as conn:
            remaining = conn.execute(
                """
                SELECT COUNT(*)
                FROM chat_messages_fts
                WHERE chat_messages_fts MATCH 'amber'
                """
            ).fetchone()
        self.soft_assert_equal(
            int(remaining[0] if remaining else -1),
            0,
            "Cascade purge should remove deleted-session terms from the index",
        )

        self.assert_no_failures()
        self.teardown_scenario()

    def _assert_value_error(
        self, operation: Callable[[], object], message: str
    ) -> None:
        try:
            operation()
        except ValueError:
            return
        self.soft_assert(False, message)

    def _assert_lookup_error(
        self, operation: Callable[[], object], message: str
    ) -> None:
        try:
            operation()
        except LookupError:
            return
        self.soft_assert(False, message)


def _message(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _tool_result(tool_name: str, content: object, tool_call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content=content,
                tool_call_id=tool_call_id,
            )
        ]
    )
