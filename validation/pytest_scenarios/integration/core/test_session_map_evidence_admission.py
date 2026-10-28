"""Mixed retrieval messages exclude untrusted evidence from map citations."""

import hashlib

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_map_batch15_inputs import (
    seed_mixed_evidence,
)

pytestmark = pytest.mark.scenario


async def test_mixed_retrieval_evidence_admission(app: Application) -> None:
    vault = app.vault("SessionMapEvidenceAdmissionVault")
    await app.start()
    from core.memory.session_map.evidence import (
        build_session_map_evidence,
        resolve_previous_map_excluded_sources,
    )
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapProvenanceError,
        SessionMapTrajectory,
        SourceRange,
        validate_session_map_provenance,
    )
    from core.memory.session_map.retained_evidence import (
        project_retained_session_map_evidence,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "map-evidence-message-variants"
    retrieval = seed_mixed_evidence(store, vault.name, session)
    raw = store.get_stored_messages(session, vault.name, mode="raw")
    envelope = build_session_map_evidence(
        session_id=session,
        vault_name=vault.name,
        history_revision=store.get_session_history_revision(session, vault.name),
        stored_messages=raw,
        model_messages=[row.message for row in raw],
    )
    assert (
        envelope.source_start_sequence_index,
        envelope.source_end_sequence_index,
        envelope.message_count,
    ) == (0, 5, 6)
    assert (
        envelope.source_digest
        == hashlib.sha256(
            "\n".join(row.message_json for row in raw).encode()
        ).hexdigest()
    )
    assert envelope.citable_source_ranges == (
        SourceRange(start=0, end=0),
        SourceRange(start=2, end=3),
        SourceRange(start=5, end=5),
    )
    assert retrieval not in envelope.projected_text
    retained = project_retained_session_map_evidence(raw)
    assert [item.sequence_index for item in retained] == [0, 2, 3, 5]
    assert retrieval not in "\n".join(item.content_text for item in retained)
    assert (
        "Eligible observed file." in envelope.projected_text
        and "Eligible mixed assistant finding." in envelope.projected_text
    )

    def draft(*sources):
        return SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="Evidence-backed trajectory.", sources=sources
            )
        )

    valid = draft(
        SourceRange(start=0, end=0),
        SourceRange(start=2, end=3),
        SourceRange(start=5, end=5),
    )
    validate_session_map_provenance(valid, evidence=(envelope,))
    for source in (
        SourceRange(start=1, end=1),
        SourceRange(start=4, end=4),
        SourceRange(start=0, end=5),
    ):
        with pytest.raises(SessionMapProvenanceError):
            validate_session_map_provenance(draft(source), evidence=(envelope,))
    previous = draft(SourceRange(start=0, end=5))
    excluded = resolve_previous_map_excluded_sources(
        store=store, session_id=session, vault_name=vault.name, previous_map=previous
    )
    assert excluded == tuple(
        SourceRange(start=index, end=index) for index in (1, 2, 3, 4, 5)
    )
    validate_session_map_provenance(
        draft(SourceRange(start=0, end=0)),
        evidence=(),
        previous_map=previous,
        excluded_source_ranges=excluded,
    )
    with pytest.raises(SessionMapProvenanceError):
        validate_session_map_provenance(
            draft(SourceRange(start=2, end=2)),
            evidence=(),
            previous_map=previous,
            excluded_source_ranges=excluded,
        )
    validate_session_map_provenance(
        draft(SourceRange(start=2, end=3)),
        evidence=retained,
        previous_map=previous,
        excluded_source_ranges=excluded,
    )
