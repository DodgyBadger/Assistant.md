"""Deterministic bounded session-map and provenance validation contract."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class SessionMapSchemaScenario(BaseScenario):
    """Keep authored map state bounded and grounded in supplied evidence."""

    async def test_scenario(self) -> None:
        from core.chat.compaction import CanonicalEvictionEnvelope
        from core.memory.session_map.authoring import (
            build_session_map_authoring_prompt,
        )
        from core.memory.session_map.models import (
            MAX_SESSION_MAP_ENTRIES,
            SessionMapDraft,
            SessionMapEntry,
            SessionMapProvenanceError,
            SourceRange,
            validate_session_map_provenance,
        )

        envelopes = (
            _envelope(
                CanonicalEvictionEnvelope,
                envelope_id="envelope-a",
                start=10,
                end=13,
            ),
            _envelope(
                CanonicalEvictionEnvelope,
                envelope_id="envelope-b",
                start=14,
                end=17,
            ),
        )
        previous = SessionMapDraft(
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
            )
        )
        authored = SessionMapDraft(
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
            )
        )
        validated = validate_session_map_provenance(
            authored,
            envelopes=envelopes,
            previous_map=previous,
        )
        assert validated is authored

        prompt_payload = json.loads(
            build_session_map_authoring_prompt(
                previous_map=previous,
                envelopes=envelopes,
            )
        )
        assert prompt_payload["prompt_contract_version"] == "eviction-map-v1"
        assert prompt_payload["previous_map"] == previous.model_dump(mode="json")
        assert [
            item["source_range"] for item in prompt_payload["new_evidence_envelopes"]
        ] == [{"start": 10, "end": 13}, {"start": 14, "end": 17}]
        assert prompt_payload["new_evidence_envelopes"][0]["projected_text"] == (
            "Evidence for 10-13"
        )

        try:
            build_session_map_authoring_prompt(
                previous_map=previous,
                envelopes=(),
            )
        except ValueError as exc:
            assert "evidence envelope" in str(exc)
        else:
            raise AssertionError("Authoring without new evidence should fail")

        unsupported = SessionMapDraft(
            entries=(
                SessionMapEntry(
                    id="invented_claim",
                    kind="decision",
                    state="active",
                    basis="user_established",
                    text="An unsupported decision.",
                    sources=(SourceRange(start=99, end=100),),
                ),
            )
        )
        try:
            validate_session_map_provenance(
                unsupported,
                envelopes=envelopes,
                previous_map=previous,
            )
        except SessionMapProvenanceError as exc:
            assert "invented_claim" in str(exc)
            assert "99-100" in str(exc)
        else:
            raise AssertionError("Out-of-scope source ranges should be rejected")

        try:
            SessionMapDraft(
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
                )
            )
        except ValueError as exc:
            assert "orientation" in str(exc).lower()
        else:
            raise AssertionError("A map should contain at most one orientation")

        try:
            SessionMapDraft(
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
                )
            )
        except ValueError:
            pass
        else:
            raise AssertionError("The map entry budget should be enforced")

        try:
            SessionMapDraft(
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
                )
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
    envelope_type: type,
    *,
    envelope_id: str,
    start: int,
    end: int,
):
    return envelope_type(
        envelope_id=envelope_id,
        session_id="session-map-schema",
        vault_name="SessionMapSchemaVault",
        history_revision=1,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        message_count=end - start + 1,
        estimated_tokens=100,
        projected_text=f"Evidence for {start}-{end}",
        source_digest=f"digest-{start}-{end}",
    )
