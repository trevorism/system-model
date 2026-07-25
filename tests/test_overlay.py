"""Authored-overlay split/merge round-trip — the honesty guarantee for capabilities.md."""
from systemmodel.core.overlay import (
    PLACEHOLDER, merge_authored, region_ids, split_authored,
)

DERIVED = f"""# Capabilities

#### As anyone, I can submit an event. <!-- cap:event.send -->
↳ `POST /event`

<!-- intent:event.send -->
{PLACEHOLDER}
<!-- /intent -->

#### As an admin, I can remove a topic. <!-- cap:topic.delete -->
↳ `DELETE /topic/{{name}}`

<!-- intent:topic.delete -->
{PLACEHOLDER}
<!-- /intent -->
"""


def test_split_ignores_placeholders():
    skeleton, authored = split_authored(DERIVED)
    assert authored == {}  # unfilled slots are not authored content
    assert skeleton == DERIVED  # canonical form of an all-placeholder doc is itself


def test_merge_reinjects_authored_and_keeps_placeholders():
    authored = {"event.send": "> intent: the platform event spine."}
    merged = merge_authored(DERIVED, authored)
    assert "> intent: the platform event spine." in merged
    assert merged.count(PLACEHOLDER) == 1  # topic.delete still a stub


def test_prose_edit_is_not_drift():
    """A filled-in intent must collapse to the same skeleton as the derived body."""
    filled = merge_authored(DERIVED, {"event.send": "> intent: anything at all"})
    assert split_authored(filled)[0] == split_authored(DERIVED)[0]


def test_round_trip_preserves_prose():
    prose = "> intent: multi\n> line\n> narrative"
    filled = merge_authored(DERIVED, {"event.send": prose})
    _, recovered = split_authored(filled)
    assert recovered["event.send"] == prose


def test_dropped_detection_via_region_ids():
    """When a capability disappears, its authored id is no longer among the new region ids."""
    _, authored = split_authored(merge_authored(DERIVED, {"topic.delete": "> intent: gone soon"}))
    new_derived = DERIVED.replace(
        "#### As an admin, I can remove a topic. <!-- cap:topic.delete -->", ""
    ).replace(f"<!-- intent:topic.delete -->\n{PLACEHOLDER}\n<!-- /intent -->", "")
    dropped = [rid for rid in authored if rid not in region_ids(new_derived)]
    assert dropped == ["topic.delete"]
