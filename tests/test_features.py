"""The feature layer: parsing a decomposition, and never losing a slug or a promotion to one."""
from pathlib import Path

from systemmodel.core.requirements import render as render_requirements
from systemmodel.core.features import (
    Feature, load, nodes, parse_body, parse_decomposition, prose, reconcile, slugify,
)
from systemmodel.core.requirements import AUTHORED, VERIFIED, Requirement, parse

DECOMPOSITION = """## employment-gap-marking -- Career Gap Detection
Makes unemployment gaps visible rather than silently closing them up.
R1. Any interval beyond the threshold must render as blank proportional width.
    -> fillInGaps, GAP_THRESHOLD_DAYS
R2. Gap segments must be excluded from the employer legend.
    -> GAP

## timeline-scaling — Proportional Layout
Normalises any span to one fixed drawing width.
R1. Total elapsed span must normalise to a fixed width.
    -> MAX_PIXEL_LENGTH

## bare-slug-only
A feature the agent gave no title.
R1. Something must hold.
    -> Thing
"""

INDEX = {"fillInGaps": {"body": "a"}, "GAP_THRESHOLD_DAYS": {"body": "b"},
         "MAX_PIXEL_LENGTH": {"body": "c"}, "GAP": {"body": "d"}, "Thing": {"body": "e"}}


def test_decomposition_parses_every_separator_style():
    found = parse_decomposition(DECOMPOSITION)
    assert [f.slug for f in found] == [
        "employment-gap-marking", "timeline-scaling", "bare-slug-only"]
    assert [f.title for f in found] == [
        "Career Gap Detection", "Proportional Layout", "bare-slug-only"]


def test_a_hyphenated_slug_is_not_cut_at_its_first_hyphen():
    assert parse_decomposition("## a-b-c -- Title\nx\nR1. y\n    -> Z\n")[0].slug == "a-b-c"


def test_requirements_and_purpose_are_attached_to_their_feature():
    gaps = parse_decomposition(DECOMPOSITION)[0]
    assert gaps.purpose.startswith("Makes unemployment gaps visible")
    assert len(gaps.requirements) == 2
    assert gaps.requirements[0].anchors == ["fillInGaps", "GAP_THRESHOLD_DAYS"]


def test_slugify_keeps_it_filename_safe():
    assert slugify("Career Gap Detection!") == "career-gap-detection"


def _document(feature: Feature) -> str:
    """The document render() would produce: skeleton headings with the prose poured in."""
    return "\n".join([f"# Feature: {feature.slug}", "", "## Summary", "", feature.summary(),
                      "", "## Requirements", "", render_requirements(feature.requirements), ""])


def test_a_feature_document_round_trips():
    original = Feature("timeline-scaling", "Proportional Layout", "Normalises any span.",
                       [Requirement(id="R1", body="Must normalise.", anchors=["MAX_PIXEL_LENGTH"])])
    recovered = parse_body(_document(original))
    assert recovered.title == original.title
    assert recovered.purpose == original.purpose
    assert recovered.requirements == original.requirements
    assert recovered.proposed is True


def test_a_dropped_slug_holding_only_description_is_pruned():
    """Its prose is never re-synthesized again, so keeping it means asserting a stale claim."""
    prior = {f.slug: f for f in parse_decomposition(DECOMPOSITION)}
    fresh = parse_decomposition("## timeline-scaling -- Proportional Layout\nStill here.\n"
                                "R1. Must normalise.\n    -> MAX_PIXEL_LENGTH\n")

    result = {f.slug: f for f in reconcile(prior, fresh, INDEX)}

    assert set(result) == {"timeline-scaling"}
    assert result["timeline-scaling"].proposed is True


def test_a_dropped_slug_holding_authored_intent_is_kept_and_flagged():
    """Pruning binding intent the fresh cut never restated would be silent, unrecoverable loss."""
    prior = {"employment-gap-marking": Feature(
        "employment-gap-marking", "Career Gap Detection", "Makes gaps visible.",
        [Requirement(id="R1", body="Disposable description."),
         Requirement(id="R2", body="Binding intent.", origin=AUTHORED, state=VERIFIED)])}
    fresh = parse_decomposition("## timeline-scaling -- Proportional Layout\nStill here.\n"
                                "R1. Must normalise.\n    -> MAX_PIXEL_LENGTH\n")

    result = {f.slug: f for f in reconcile(prior, fresh, INDEX)}

    assert set(result) == {"timeline-scaling", "employment-gap-marking"}
    kept = result["employment-gap-marking"]
    assert kept.proposed is False
    assert "Superseded" in kept.summary()
    assert [r.body for r in kept.requirements if r.is_authored] == ["Binding intent."]


def test_the_legacy_supersession_marker_still_parses():
    """Model dirs written before the marker changed must not fold it into the purpose prose."""
    legacy = ("**Career Gap Detection**\n\n"
              "> _No longer proposed by the latest decomposition — keep it or delete the file._\n\n"
              "Makes gaps visible.")
    recovered = parse_body(f"# Feature: x\n\n## Summary\n\n{legacy}\n\n## Requirements\n\n")

    assert recovered.proposed is False
    assert recovered.purpose == "Makes gaps visible."


def test_a_promoted_requirement_survives_a_fresh_decomposition():
    prior = {"timeline-scaling": Feature(
        "timeline-scaling", "Proportional Layout", "old purpose",
        [Requirement(id="R1", body="Disposable."),
         Requirement(id="R2", body="Binding intent.", origin=AUTHORED, state=VERIFIED)])}
    fresh = parse_decomposition("## timeline-scaling -- Renamed Title\nNew purpose.\n"
                                "R1. Freshly described.\n    -> MAX_PIXEL_LENGTH\n")

    scaling = reconcile(prior, fresh, INDEX)[0]
    held = [r for r in scaling.requirements if r.is_authored]

    assert [(r.id, r.body) for r in held] == [("R2", "Binding intent.")]
    assert "Freshly described." in [r.body for r in scaling.requirements]
    assert "Disposable." not in [r.body for r in scaling.requirements]
    assert scaling.title == "Renamed Title"  # prose may change; the slug may not


def test_the_node_skeleton_carries_only_the_anchor():
    """The title is synthesized, so a skeleton heading would mean two H1s per document."""
    feature = parse_decomposition(DECOMPOSITION)[0]
    body = nodes([feature], INDEX)[0].body
    assert "## Summary" in body and "## Requirements" in body
    assert "<!--" not in body
    assert feature.title not in body  # the synthesized title is prose, not skeleton


def test_a_feature_node_moves_only_when_its_anchored_code_moves():
    feature = parse_decomposition(DECOMPOSITION)[0]
    unchanged = nodes([feature], INDEX)[0].content_hash()
    moved = nodes([feature], dict(INDEX, fillInGaps={"body": "changed"}))[0].content_hash()
    assert unchanged != moved


def test_loading_from_disk_recovers_slugs_and_promotions(tmp_path: Path):
    root = tmp_path / "svc"
    (root / "features").mkdir(parents=True)
    feature = Feature("timeline-scaling", "Proportional Layout", "Normalises.",
                      [Requirement(id="R1", body="Held.", origin=AUTHORED, state=VERIFIED)])
    (root / feature.path).write_text(_document(feature), encoding="utf-8")

    loaded = load(root)
    assert list(loaded) == ["timeline-scaling"]
    assert loaded["timeline-scaling"].requirements[0].is_authored


def test_prose_is_keyed_by_document_path():
    feature = parse_decomposition(DECOMPOSITION)[0]
    payload = prose([feature])
    assert list(payload) == ["features/employment-gap-marking.md"]
    sections = payload["features/employment-gap-marking.md"]
    assert "Career Gap Detection" in sections["Summary"]
    assert parse(sections["Requirements"])[0].anchors == ["fillInGaps", "GAP_THRESHOLD_DAYS"]


def test_keeping_existing_features_does_not_clone_promotions(tmp_path: Path):
    """`merge` treats its fresh argument as new description; prior records are not that."""
    from systemmodel.core.features import keep_existing
    prior = {"f": Feature("f", "Title", "purpose", [
        Requirement(id="R1", body="Described."),
        Requirement(id="R2", body="Binding.", origin=AUTHORED, state=VERIFIED)])}

    kept = keep_existing(prior, INDEX)[0]

    assert [(r.id, r.origin) for r in kept.requirements] == [("R1", "derived"), ("R2", AUTHORED)]
