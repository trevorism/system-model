"""Hash gating: synthesis fires only when the evidence beneath a section actually moved.

This is the cost control for the whole tool. Re-deriving 54 repos has to be free when nothing
changed, or nobody runs it — and then the model is stale exactly when it matters.
"""
import json
from pathlib import Path

import systemmodel.core.synth as synth
from systemmodel.core.evidence import Evidence
from systemmodel.core.requirements import (
    AUTHORED, UNVERIFIED, VERIFIED, Requirement, parse, render,
)
from systemmodel.core.schema import Level, Node

SETTLED = "aaaa111122223333"
MOVED = "bbbb444455556666"

FRESH_PROSE = """R1. Something newly synthesized about the system.
    -> NewController.handle
"""


def _node(purpose: str = SETTLED, requirements: str = SETTLED) -> Node:
    return Node(Level.L1, "overview", "overview", "overview.md",
                "\n".join(["# svc", "", "## Purpose", "", "## Requirements", ""]),
                synth_sections={"Purpose": purpose, "Requirements": requirements})


def _setup(monkeypatch, tmp_path: Path, purpose: str, requirements: str) -> Path:
    model_dir = tmp_path / "systemmodel"
    repo = tmp_path / "dev" / "svc"
    (model_dir / "svc").mkdir(parents=True)
    repo.mkdir(parents=True)
    monkeypatch.setenv("SYSTEMMODEL_DIR", str(model_dir))
    (model_dir / "svc" / "overview.md").write_text(
        "\n".join(["# svc", "", "## Purpose", "", purpose, "",
                   "## Requirements", "", requirements, ""]),
        encoding="utf-8")
    return repo


def _recorded(purpose: str = SETTLED, requirements: str = SETTLED) -> dict:
    return {"overview.md": {"regions": {"purpose": purpose, "requirements": requirements},
                            "requirements": {}}}


def _resolve(repo, nodes, recorded):
    return synth.resolve(repo, nodes, Evidence(target="svc", sections={}, shared={}),
                         recorded=recorded, on_log=lambda *a: None)


def _must_not_fire(*args, **kwargs):
    raise AssertionError("synthesis fired when the evidence had not moved")


def test_a_verdict_cannot_outlive_the_code_it_judged(monkeypatch, tmp_path):
    """A re-derive must demote a verified feature record whose anchored code has moved.

    The decomposition path only sees that movement if it hydrates the recorded anchor hashes.
    Without them every prior record looks never-hashed, the hash is silently re-baselined, and
    the record goes on claiming `verified` about code that has since changed.
    """
    model_dir = tmp_path / "systemmodel"
    repo = tmp_path / "dev" / "svc"
    (model_dir / "svc" / "features").mkdir(parents=True)
    repo.mkdir(parents=True)
    monkeypatch.setenv("SYSTEMMODEL_DIR", str(model_dir))

    held = Requirement(id="R2", body="Binding.", anchors=["X"], origin=AUTHORED,
                       state=VERIFIED, finding="Checked the code as it stood then.")
    (model_dir / "svc" / "features" / "f.md").write_text(
        "\n".join(["# Feature: f", "", "## Summary", "", "**Title**", "", "Purpose.", "",
                   "## Requirements", "", render([held]), ""]), encoding="utf-8")

    evidence = Evidence(target="svc", sections={}, shared={})
    (model_dir / "svc" / "MANIFEST.json").write_text(json.dumps({
        "decomposition": evidence.section_hash("requirements"),
        "nodes": [{"path": "features/f.md",
                   "requirements": {"R2": "the-hash-recorded-when-it-was-verified"}}],
    }), encoding="utf-8")

    resolved, _stamp, regenerated = synth.decompose(
        repo, evidence, {"X": {"body": "the code has since changed"}}, on_log=lambda *a: None)

    assert regenerated is False  # the free path: no re-cut, no agent call
    demoted = resolved[0].requirements[0]
    assert demoted.is_authored  # the intent survives
    assert demoted.state == UNVERIFIED  # the verdict does not
    assert demoted.finding is None


def test_unchanged_evidence_makes_no_agent_call(monkeypatch, tmp_path):
    repo = _setup(monkeypatch, tmp_path, "Existing purpose.",
                  render([Requirement(id="R1", body="An obligation.", anchors=["X"])]))
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", _must_not_fire)

    prose, _hashes, regenerated = _resolve(repo, [_node()], _recorded())

    assert regenerated == []
    assert prose["overview.md"]["Purpose"] == "Existing purpose."


def test_moved_evidence_regenerates_only_that_section(monkeypatch, tmp_path):
    repo = _setup(monkeypatch, tmp_path, "Existing purpose.",
                  render([Requirement(id="R1", body="An obligation.", anchors=["X"])]))
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: FRESH_PROSE)

    prose, _hashes, regenerated = _resolve(
        repo, [_node(requirements=MOVED)], _recorded())

    assert regenerated == ["overview.md:requirements"]
    assert prose["overview.md"]["Purpose"] == "Existing purpose."  # untouched
    assert "Something newly synthesized" in prose["overview.md"]["Requirements"]


def test_prose_that_breaks_out_of_its_section_is_rejected(monkeypatch, tmp_path):
    """A `##` in the body would end the section early and orphan everything after it."""
    repo = _setup(monkeypatch, tmp_path, "Existing purpose.", "")
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: "## Oops\nrunaway heading")

    prose, _hashes, regenerated = _resolve(repo, [_node(purpose=MOVED)], _recorded())

    assert regenerated == []
    assert prose["overview.md"]["Purpose"] == "Existing purpose."


def test_a_missing_cli_keeps_what_is_on_disk(monkeypatch, tmp_path):
    repo = _setup(monkeypatch, tmp_path, "Existing purpose.", "")
    monkeypatch.setattr(synth, "available", lambda: False)

    prose, _hashes, regenerated = _resolve(repo, [_node(purpose=MOVED)], _recorded())

    assert regenerated == []
    assert prose["overview.md"]["Purpose"] == "Existing purpose."


def test_a_failed_agent_call_keeps_prior_prose(monkeypatch, tmp_path):
    repo = _setup(monkeypatch, tmp_path, "Existing purpose.", "")
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: None)

    prose, _hashes, regenerated = _resolve(repo, [_node(purpose=MOVED)], _recorded())

    assert regenerated == []
    assert prose["overview.md"]["Purpose"] == "Existing purpose."


def test_authored_intent_survives_a_regenerated_requirements_section(monkeypatch, tmp_path):
    prior = render([Requirement(id="R1", body="Disposable."),
                    Requirement(id="R2", body="Binding.", origin=AUTHORED, state=VERIFIED)])
    repo = _setup(monkeypatch, tmp_path, "Existing purpose.", prior)
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: FRESH_PROSE)

    prose, _hashes, _regen = _resolve(repo, [_node(requirements=MOVED)], _recorded())

    records = parse(prose["overview.md"]["Requirements"])
    held = [r for r in records if r.is_authored]
    assert [(r.id, r.body, r.state) for r in held] == [("R2", "Binding.", VERIFIED)]
    assert "Disposable." not in [r.body for r in records]


def test_a_legacy_document_migrates_without_an_agent_call(monkeypatch, tmp_path):
    """The comment-delimited form is read, not written — that is what makes migration free."""
    model_dir = tmp_path / "systemmodel"
    repo = tmp_path / "dev" / "svc"
    (model_dir / "svc").mkdir(parents=True)
    repo.mkdir(parents=True)
    monkeypatch.setenv("SYSTEMMODEL_DIR", str(model_dir))
    (model_dir / "svc" / "overview.md").write_text(
        f"# svc\n\n<!-- synth:purpose evidence={SETTLED} -->\nOld purpose prose.\n"
        f"<!-- /synth -->\n\n## Requirements\n\n"
        f"<!-- synth:requirements evidence={SETTLED} -->\n"
        f"<!-- req:R1 origin=authored anchors=abc123 state=verified -->\n"
        f"**R1.** Binding intent from the old format.\n→ `Thing`\n<!-- /req -->\n"
        f"<!-- /synth -->\n", encoding="utf-8")
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", _must_not_fire)

    prose, _hashes, regenerated = _resolve(repo, [_node()], {})

    assert regenerated == []
    assert prose["overview.md"]["Purpose"] == "Old purpose prose."
    migrated = parse(prose["overview.md"]["Requirements"])
    assert [(r.id, r.origin, r.state) for r in migrated] == [("R1", AUTHORED, VERIFIED)]
    assert "<!--" not in prose["overview.md"]["Requirements"]
