"""Deterministic bounded session-map and provenance validation contract."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.memory.session_map.evidence import SessionMapEvidence  # noqa: E402
from core.memory.session_map.models import SourceRange  # noqa: E402
from validation.core.base_scenario import BaseScenario


class SessionMapSchemaScenario(BaseScenario):
    """Keep authored map state bounded and grounded in supplied evidence."""

    async def test_scenario(self) -> None:
        from core.memory.session_map.authoring import (
            build_session_map_authoring_prompt,
        )
        from core.memory.session_map.models import (
            MAX_SESSION_MAP_ENTRIES,
            MAX_SESSION_MAP_TRAJECTORY_TEXT_CHARS,
            SessionMapDraft,
            SessionMapEntry,
            SessionMapProvenanceError,
            SessionMapTrajectory,
            validate_session_map_provenance,
        )

        envelopes = (
            _envelope(
                evidence_id="envelope-a",
                start=10,
                end=13,
            ),
            _envelope(
                evidence_id="envelope-b",
                start=14,
                end=17,
            ),
        )
        previous = SessionMapDraft(
            schema_version=1,
            entries=(
                SessionMapEntry(
                    id="redevelopment_orientation",
                    kind="orientation",
                    state="active",
                    basis="user_established",
                    text="The co-op is evaluating neighbouring redevelopment risks.",
                    sources=(SourceRange(start=2, end=5),),
                ),
                SessionMapEntry(
                    id="wait_for_documents",
                    kind="next_action",
                    state="active",
                    basis="user_established",
                    text="Wait for the written materials before deciding next steps.",
                    sources=(SourceRange(start=4, end=5),),
                ),
            ),
        )
        authored = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The work moved from initial proposal review into legal review after the City land issue emerged.",
                sources=(
                    SourceRange(start=2, end=5),
                    SourceRange(start=10, end=14),
                ),
            ),
            entries=(
                SessionMapEntry(
                    id="redevelopment_orientation",
                    kind="orientation",
                    state="active",
                    basis="user_established",
                    text="The co-op has moved from initial contact into legal review.",
                    sources=(
                        SourceRange(start=2, end=5),
                        SourceRange(start=10, end=13),
                    ),
                ),
                SessionMapEntry(
                    id="wait_for_documents",
                    kind="next_action",
                    state="superseded",
                    basis="user_established",
                    text="Waiting for developer documents is no longer the next action.",
                    sources=(
                        SourceRange(start=4, end=5),
                        SourceRange(start=10, end=11),
                    ),
                ),
                SessionMapEntry(
                    id="wait_for_counsel",
                    kind="next_action",
                    state="active",
                    basis="user_established",
                    text="Wait for counsel's initial review before further engagement.",
                    sources=(SourceRange(start=12, end=13),),
                ),
                SessionMapEntry(
                    id="city_owned_land",
                    kind="constraint",
                    state="active",
                    basis="user_established",
                    text="The City owns the land beneath the co-op.",
                    sources=(SourceRange(start=14, end=14),),
                ),
            ),
        )
        validated = validate_session_map_provenance(
            authored,
            evidence=envelopes,
            previous_map=previous,
        )
        assert validated is authored

        prompt_payload = json.loads(
            build_session_map_authoring_prompt(
                previous_map=previous,
                new_evidence=envelopes,
            )
        )
        assert prompt_payload["prompt_contract_version"] == "eviction-map-v10"
        assert prompt_payload["user_focus"] is None
        assert prompt_payload["previous_map"] == previous.model_dump(mode="json")
        assert [
            item["source_range"] for item in prompt_payload["new_evidence_envelopes"]
        ] == [{"start": 10, "end": 13}, {"start": 14, "end": 17}]
        assert prompt_payload["new_evidence_envelopes"][0]["projected_text"] == (
            "Evidence for 10-13"
        )
        focused_payload = json.loads(
            build_session_map_authoring_prompt(
                previous_map=previous,
                new_evidence=envelopes,
                focus="Preserve the unresolved legal question.",
            )
        )
        assert focused_payload["user_focus"] == (
            "Preserve the unresolved legal question."
        )

        try:
            build_session_map_authoring_prompt(
                previous_map=previous,
                new_evidence=(),
            )
        except ValueError as exc:
            assert "canonical evidence" in str(exc)
        else:
            raise AssertionError("Authoring without new evidence should fail")

        unsupported = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The prior legal-review trajectory remains current.",
                sources=(SourceRange(start=10, end=10),),
            ),
            entries=(
                SessionMapEntry(
                    id="invented_claim",
                    kind="decision",
                    state="active",
                    basis="user_established",
                    text="An unsupported decision.",
                    sources=(SourceRange(start=99, end=100),),
                ),
            ),
        )
        try:
            validate_session_map_provenance(
                unsupported,
                evidence=envelopes,
                previous_map=previous,
            )
        except SessionMapProvenanceError as exc:
            assert "invented_claim" in str(exc)
            assert "99-100" in str(exc)
        else:
            raise AssertionError("Out-of-scope source ranges should be rejected")

        try:
            validate_session_map_provenance(
                SessionMapDraft(
                    trajectory=SessionMapTrajectory(
                        text="An unsupported trajectory.",
                        sources=(SourceRange(start=98, end=98),),
                    ),
                    entries=authored.entries,
                ),
                evidence=envelopes,
                previous_map=previous,
            )
        except SessionMapProvenanceError as exc:
            assert "trajectory" in str(exc)
            assert "98-98" in str(exc)
        else:
            raise AssertionError("Out-of-scope trajectory provenance should fail")

        legacy = SessionMapDraft.model_validate(
            {
                "schema_version": 1,
                "entries": previous.model_dump(mode="json")["entries"],
            }
        )
        assert legacy.trajectory is None

        prior_schema = SessionMapDraft.model_validate(
            {
                "schema_version": 2,
                "trajectory": {
                    "text": "An old checkpoint remains readable.",
                    "sources": [{"start": 10, "end": 10}],
                },
                "entries": [
                    {
                        "id": "historical_assistant_decision",
                        "kind": "decision",
                        "state": "active",
                        "basis": "assistant_proposed",
                        "text": "A historical combination predating stricter admission.",
                        "sources": [{"start": 10, "end": 10}],
                    }
                ],
            }
        )
        assert prior_schema.schema_version == 2

        conservative = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="Research exposed an alternative without adopting it.",
                sources=(SourceRange(start=10, end=12),),
            ),
            entries=(
                SessionMapEntry(
                    id="alternate_site",
                    kind="option",
                    state="active",
                    basis="assistant_proposed",
                    text="The alternate site remains a candidate, not a decision.",
                    sources=(SourceRange(start=10, end=10),),
                ),
                SessionMapEntry(
                    id="site_cost",
                    kind="finding",
                    state="active",
                    basis="tool_observed",
                    text="The alternate site's reported cost exceeds the budget.",
                    sources=(SourceRange(start=11, end=12),),
                ),
            ),
        )
        assert conservative.schema_version == 3

        for kind, basis in (
            ("goal", "assistant_proposed"),
            ("decision", "assistant_proposed"),
            ("next_action", "tool_observed"),
            ("constraint", "assistant_proposed"),
            ("artifact", "assistant_proposed"),
        ):
            try:
                SessionMapDraft(
                    trajectory=SessionMapTrajectory(
                        text="The candidate entry is not established.",
                        sources=(SourceRange(start=10, end=10),),
                    ),
                    entries=(
                        SessionMapEntry(
                            id=f"invalid_{kind}",
                            kind=kind,
                            state="active",
                            basis=basis,
                            text="This entry should use a less committal kind.",
                            sources=(SourceRange(start=10, end=10),),
                        ),
                    ),
                )
            except ValueError as exc:
                assert kind in str(exc)
            else:
                raise AssertionError(f"Invalid {kind}/{basis} admission should fail")

        try:
            SessionMapDraft(entries=authored.entries)
        except ValueError as exc:
            assert "requires a trajectory" in str(exc)
        else:
            raise AssertionError("Current nonempty maps should require a trajectory")

        try:
            SessionMapTrajectory(
                text="x" * (MAX_SESSION_MAP_TRAJECTORY_TEXT_CHARS + 1),
                sources=(SourceRange(start=10, end=10),),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("The narrative trajectory budget should be enforced")

        try:
            SessionMapDraft(
                schema_version=1,
                entries=(
                    SessionMapEntry(
                        id="first_orientation",
                        kind="orientation",
                        state="active",
                        basis="user_established",
                        text="First orientation.",
                        sources=(SourceRange(start=10, end=10),),
                    ),
                    SessionMapEntry(
                        id="second_orientation",
                        kind="orientation",
                        state="active",
                        basis="user_established",
                        text="Second orientation.",
                        sources=(SourceRange(start=11, end=11),),
                    ),
                ),
            )
        except ValueError as exc:
            assert "orientation" in str(exc).lower()
        else:
            raise AssertionError("A map should contain at most one orientation")

        try:
            SessionMapDraft(
                schema_version=1,
                entries=tuple(
                    SessionMapEntry(
                        id=f"goal_{index}",
                        kind="goal",
                        state="active",
                        basis="user_established",
                        text=f"Goal {index}",
                        sources=(SourceRange(start=10, end=10),),
                    )
                    for index in range(MAX_SESSION_MAP_ENTRIES + 1)
                ),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("The map entry budget should be enforced")

        try:
            SessionMapDraft(
                schema_version=1,
                entries=(
                    SessionMapEntry(
                        id="duplicate_entry",
                        kind="goal",
                        state="active",
                        basis="user_established",
                        text="First duplicate.",
                        sources=(SourceRange(start=10, end=10),),
                    ),
                    SessionMapEntry(
                        id="duplicate_entry",
                        kind="constraint",
                        state="active",
                        basis="user_established",
                        text="Second duplicate.",
                        sources=(SourceRange(start=11, end=11),),
                    ),
                ),
            )
        except ValueError as exc:
            assert "unique" in str(exc).lower()
        else:
            raise AssertionError("Map entry IDs should be globally unique")

        try:
            SessionMapEntry(
                id="decision",
                kind="decision",
                state="active",
                basis="user_established",
                text="A generic identifier obscures stable subject identity.",
                sources=(SourceRange(start=10, end=10),),
            )
        except ValueError as exc:
            assert "semantic subject" in str(exc)
        else:
            raise AssertionError("Generic category names should not be valid IDs")

        self.assert_no_failures()


def _envelope(
    *,
    evidence_id: str,
    start: int,
    end: int,
) -> SessionMapEvidence:
    return SessionMapEvidence(
        evidence_id=evidence_id,
        session_id="session-map-schema",
        vault_name="SessionMapSchemaVault",
        history_revision=1,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        message_count=end - start + 1,
        estimated_tokens=100,
        projected_text=f"Evidence for {start}-{end}",
        source_digest=f"digest-{start}-{end}",
        citable_source_ranges=(SourceRange(start=start, end=end),),
    )
