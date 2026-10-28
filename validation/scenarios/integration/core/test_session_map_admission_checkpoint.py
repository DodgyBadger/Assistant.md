"""Admission checkpoints preserve only verified citations from mixed messages."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_admission_extra_inputs import (
    append_recursive_windows,
    draft,
)
from validation.scenarios.support.session_map_batch15_inputs import (
    seed_mixed_evidence,
)

pytestmark = pytest.mark.scenario


async def test_recursive_windows_and_verified_admission_marker(
    app: Application,
) -> None:
    vault = app.vault("MapAdmissionCheckpointVault")
    await app.start()
    from core.memory.session_map.checkpoints import (
        commit_session_map_context_checkpoint,
    )
    from core.memory.session_map.evidence import (
        build_session_map_evidence,
        resolve_previous_map_excluded_sources,
    )
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapProvenanceError,
        SourceRange,
        validate_session_map_provenance,
    )
    from core.memory.session_map.retained_evidence import (
        project_retrieved_session_map_evidence,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "admission-checkpoint"
    seed_mixed_evidence(store, vault.name, session)
    raw = store.get_stored_messages(session, vault.name, mode="raw")
    append_recursive_windows(store, vault.name, session, raw)
    current = store.get_stored_messages(session, vault.name, mode="raw")
    retrieved = project_retrieved_session_map_evidence(
        store=store, session_id=session, vault_name=vault.name, retained=current[-2:]
    )
    assert [item.sequence_index for item in retrieved.messages] == [0]
    revision = store.get_session_history_revision(session, vault.name)
    envelope = build_session_map_evidence(
        session_id=session,
        vault_name=vault.name,
        history_revision=revision,
        stored_messages=raw,
        model_messages=[row.message for row in raw],
    )
    valid = draft(
        SourceRange(start=0, end=0),
        SourceRange(start=2, end=3),
        SourceRange(start=5, end=5),
    )
    committed = commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=valid,
        previous_map=SessionMapDraft(),
        new_evidence=(envelope,),
        expected_history_revision=revision,
        message_count_before=8,
        source="validation",
    )
    assert (
        json.loads(committed.checkpoint.metadata_json or "{}")[
            "evidence_admission_version"
        ]
        == 1
    )
    exclusions = resolve_previous_map_excluded_sources(
        store=store, session_id=session, vault_name=vault.name, previous_map=valid
    )
    assert exclusions == ()
    validate_session_map_provenance(
        valid, evidence=(), previous_map=valid, excluded_source_ranges=exclusions
    )
    for index in (1, 2):
        different = draft(SourceRange(start=index, end=index))
        excluded = resolve_previous_map_excluded_sources(
            store=store,
            session_id=session,
            vault_name=vault.name,
            previous_map=different,
        )
        assert excluded == (SourceRange(start=index, end=index),)
        with pytest.raises(SessionMapProvenanceError):
            validate_session_map_provenance(
                different,
                evidence=(),
                previous_map=different,
                excluded_source_ranges=excluded,
            )
