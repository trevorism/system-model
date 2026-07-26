"""Heading-delimited sections: the replacement for comment-delimited regions.

These carry the guarantees the old overlay tests protected — prose is never drift, the tool only
rewrites the sections it owns — plus the one the new form needs and the old one did not: prose
containing a heading would silently truncate the section.
"""
from pathlib import Path

from systemmodel.core.overlay import (
    blank_sections, contains_heading_at_or_above, replace_section, section_body, section_spans,
)
from systemmodel.core.render import INTENT_FILE, build_manifest, render
from systemmodel.core.schema import Level, Node

DOC = """# service

`service` · liveness `GET /ping`

## Purpose

What it is for.

## Requirements

### R1
An obligation.
→ `Thing`

## Wiring

- **calls** → other
"""


def test_a_section_runs_until_the_next_heading_of_equal_or_higher_level():
    assert section_body(DOC, "Purpose").strip() == "What it is for."
    assert "### R1" in section_body(DOC, "Requirements")
    assert "calls" in section_body(DOC, "Wiring")


def test_a_subsection_does_not_end_its_parent():
    """`### R1` sits inside Requirements; only `##` or `#` closes it."""
    body = section_body(DOC, "Requirements")
    assert "An obligation." in body
    assert "calls" not in body


def test_an_absent_section_is_none_rather_than_empty():
    assert section_body(DOC, "Watch out") is None


def test_replacing_a_section_leaves_its_neighbours_alone():
    updated = replace_section(DOC, "Purpose", "Something else entirely.")
    assert section_body(updated, "Purpose").strip() == "Something else entirely."
    assert section_body(updated, "Wiring") == section_body(DOC, "Wiring")
    assert updated.startswith("# service")


def test_replacing_the_last_section_keeps_the_document_well_formed():
    updated = replace_section(DOC, "Wiring", "- **calls** → nothing")
    assert updated.rstrip().endswith("- **calls** → nothing")


def test_blanking_removes_only_the_named_bodies():
    skeleton = blank_sections(DOC, ["Purpose", "Requirements"])
    assert "What it is for." not in skeleton
    assert "An obligation." not in skeleton
    assert "## Purpose" in skeleton and "## Requirements" in skeleton
    assert "calls" in skeleton  # a section the tool derives is untouched


def test_prose_carrying_a_heading_is_detected():
    """Synthesis is told not to; if it does anyway the section would end early and orphan text."""
    assert contains_heading_at_or_above("## Sneaky\nmore text", 2) is True
    assert contains_heading_at_or_above("### Fine\nmore text", 2) is False
    assert contains_heading_at_or_above("Just prose.", 2) is False


def test_headings_report_their_level_and_body_range():
    titles = [(t, level) for t, level, _, _ in section_spans(DOC)]
    assert ("service", 1) in titles
    assert ("Purpose", 2) in titles
    assert ("R1", 3) in titles


def _node() -> Node:
    return Node(Level.L1, "overview", "overview", "overview.md",
                "# service\n\n## Purpose\n\n## Wiring\n\n- calls → other\n",
                synth_sections={"Purpose": "abc123"})


def test_synthesized_prose_is_not_part_of_the_content_hash(tmp_path: Path):
    """A reworded purpose must never read as drift."""
    node = _node()
    before = node.content_hash()
    render(tmp_path, [node], adapter="a", target="t", generated_at="now",
           synth_prose={"overview.md": {"Purpose": "One wording."}})
    after = build_manifest([node], adapter="a", target="t", generated_at="now")
    assert after["nodes"][0]["content_hash"] == before

    render(tmp_path, [node], adapter="a", target="t", generated_at="now",
           synth_prose={"overview.md": {"Purpose": "A completely different wording."}})
    assert build_manifest([node], adapter="a", target="t",
                          generated_at="now")["nodes"][0]["content_hash"] == before


def test_prose_and_machine_state_land_in_their_separate_homes(tmp_path: Path):
    render(tmp_path, [_node()], adapter="a", target="t", generated_at="now",
           synth_prose={"overview.md": {"Purpose": "What it is for."}},
           requirement_hashes={"overview.md": {"R1": "deadbeef"}})

    written = (tmp_path / "overview.md").read_text(encoding="utf-8")
    assert "What it is for." in written
    assert "<!--" not in written  # nothing a reader has to be told to ignore

    manifest = build_manifest([_node()], adapter="a", target="t", generated_at="now",
                              requirement_hashes={"overview.md": {"R1": "deadbeef"}})
    assert manifest["nodes"][0]["regions"] == {"purpose": "abc123"}
    assert manifest["nodes"][0]["requirements"] == {"R1": "deadbeef"}


def test_intent_is_never_pruned(tmp_path: Path):
    """The one file in the tree a human owns; a regeneration must not touch it."""
    render(tmp_path, [_node()], adapter="a", target="t", generated_at="now")
    intent = tmp_path / INTENT_FILE
    intent.write_text("- make the thing require auth\n", encoding="utf-8")

    result = render(tmp_path, [], adapter="a", target="t", generated_at="now")

    assert INTENT_FILE not in result.pruned
    assert intent.read_text(encoding="utf-8") == "- make the thing require auth\n"


def test_an_untouched_intent_file_is_refreshed(tmp_path: Path):
    """Boilerplate can improve later without a hand migration."""
    from systemmodel.core.render import ensure_intent
    stale = tmp_path / INTENT_FILE
    stale.write_text("# Intent — svc\n\nPages of old instructions.\n\n## Desired updates\n\n",
                     encoding="utf-8")

    assert ensure_intent(tmp_path, "svc") is True
    assert "Pages of old instructions." not in stale.read_text(encoding="utf-8")


def test_intent_a_human_has_written_in_is_never_clobbered(tmp_path: Path):
    from systemmodel.core.render import ensure_intent
    written = tmp_path / INTENT_FILE
    body = "# Intent — svc\n\n## Desired updates\n\n- callers must be authenticated\n"
    written.write_text(body, encoding="utf-8")

    assert ensure_intent(tmp_path, "svc") is False
    assert written.read_text(encoding="utf-8") == body


def test_intent_with_applied_history_is_never_clobbered(tmp_path: Path):
    """An empty inbox does not mean unused — the history is the evidence it was used."""
    from systemmodel.core.render import ensure_intent
    written = tmp_path / INTENT_FILE
    body = ("# Intent — svc\n\n## Desired updates\n\n"
            "## Applied\n\n### 2026-01-01\n- promoted R2\n")
    written.write_text(body, encoding="utf-8")

    assert ensure_intent(tmp_path, "svc") is False
    assert written.read_text(encoding="utf-8") == body
