"""Synth regions: prose is preserved, never hashed, and the evidence anchor survives."""
from systemmodel.core.overlay import (
    SYNTH_PLACEHOLDER, merge_synth, split_regions, synth_anchor, synth_requests,
)

DERIVED = f"""# overview

{synth_anchor("purpose", "aaaa1111")}

## Requirements

{synth_anchor("requirements", "bbbb2222")}
"""


def test_placeholder_regions_are_not_synthesized_content():
    _, _, synthesized = split_regions(DERIVED)
    assert synthesized == {}


def test_requests_expose_ids_and_evidence():
    assert synth_requests(DERIVED) == {"purpose": "aaaa1111", "requirements": "bbbb2222"}


def test_merge_then_split_round_trips_prose_and_evidence():
    filled = merge_synth(DERIVED, {"purpose": "A leaf service that authenticates people."})
    _, _, synthesized = split_regions(filled)
    assert synthesized["purpose"].prose == "A leaf service that authenticates people."
    assert synthesized["purpose"].evidence == "aaaa1111"
    assert "requirements" not in synthesized


def test_prose_edit_is_not_drift():
    one = merge_synth(DERIVED, {"purpose": "first wording"})
    two = merge_synth(DERIVED, {"purpose": "an entirely different wording"})
    assert split_regions(one)[0] == split_regions(two)[0] == split_regions(DERIVED)[0]


def test_evidence_change_is_drift():
    moved = DERIVED.replace("aaaa1111", "cccc3333")
    assert split_regions(moved)[0] != split_regions(DERIVED)[0]


def test_skeleton_restores_placeholder_body():
    filled = merge_synth(DERIVED, {"purpose": "some prose"})
    skeleton = split_regions(filled)[0]
    assert SYNTH_PLACEHOLDER in skeleton
    assert "some prose" not in skeleton
