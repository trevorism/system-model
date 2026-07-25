"""Requirements flowing through synthesis: migration on reuse, preservation on regeneration."""
from pathlib import Path

import systemmodel.core.synth as synth
from systemmodel.core.evidence import Evidence
from systemmodel.core.overlay import synth_anchor
from systemmodel.core.requirements import AUTHORED, VERIFIED, Requirement, parse_blocks, render
from systemmodel.core.schema import Level, Node

LEGACY_PROSE = """R1. Ownership is decided from the signed token, never from cookies.
    -> ImageController.isOwnerOrAdmin

R2. Uploads are stamped with the authenticated identity.
    -> ImageController.upload
"""

FRESH_PROSE = """R1. Something newly synthesized about the system.
    -> NewController.handle
"""


def _node(evidence_hash: str) -> Node:
    body = "\n".join(["# Overview", "", "## Requirements", "",
                      synth_anchor("requirements", evidence_hash), ""])
    return Node(Level.L1, "overview", "overview", "overview.md", body, supports_authored=True)


def _setup(monkeypatch, tmp_path: Path, prior_region: str, evidence_hash: str) -> Path:
    """Lay down a model dir with an existing requirements region, and point the tool at it."""
    model_dir = tmp_path / "systemmodel"
    repo = tmp_path / "dev" / "svc"
    (model_dir / "svc").mkdir(parents=True)
    repo.mkdir(parents=True)
    monkeypatch.setenv("SYSTEMMODEL_DIR", str(model_dir))
    (model_dir / "svc" / "overview.md").write_text(
        "\n".join(["# Overview", "", "## Requirements", "",
                   f"<!-- synth:requirements evidence={evidence_hash} -->",
                   prior_region, "<!-- /synth -->", ""]),
        encoding="utf-8",
    )
    return repo


def _evidence() -> Evidence:
    return Evidence(target="svc", sections={"requirements": {}}, shared={})


def test_legacy_prose_is_migrated_even_though_nothing_regenerated(monkeypatch, tmp_path):
    repo = _setup(monkeypatch, tmp_path, LEGACY_PROSE, "abc123")
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: pytest_fail_if_called())

    prose, regenerated = resolve_quiet(repo, [_node("abc123")])

    assert regenerated == []  # evidence unchanged: no agent call, no cost
    records = parse_blocks(prose["overview.md"]["requirements"])
    assert [r.id for r in records] == ["R1", "R2"]


def test_authored_intent_survives_a_regeneration_that_replaces_the_description(
        monkeypatch, tmp_path):
    prior = render([
        Requirement(id="R1", body="Disposable description.", anchors=["Old"]),
        Requirement(id="R2", body="Binding intent that must not be lost.", anchors=["Keep"],
                    origin=AUTHORED, state=VERIFIED),
    ])
    repo = _setup(monkeypatch, tmp_path, prior, "01d001d001d001d0")
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: FRESH_PROSE)

    prose, regenerated = resolve_quiet(repo, [_node("beef1234beef1234")])

    assert regenerated == ["overview.md:requirements"]
    records = parse_blocks(prose["overview.md"]["requirements"])
    held = [r for r in records if r.is_authored]
    assert [(r.id, r.body, r.state) for r in held] == [
        ("R2", "Binding intent that must not be lost.", VERIFIED)]
    assert "Disposable description." not in [r.body for r in records]
    assert "Something newly synthesized about the system." in [r.body for r in records]


def test_a_failed_agent_call_does_not_wipe_authored_intent(monkeypatch, tmp_path):
    prior = render([Requirement(id="R1", body="Binding.", origin=AUTHORED)])
    repo = _setup(monkeypatch, tmp_path, prior, "01d001d001d001d0")
    monkeypatch.setattr(synth, "available", lambda: True)
    monkeypatch.setattr(synth, "_invoke", lambda *a, **k: None)  # timeout / non-zero exit

    prose, regenerated = resolve_quiet(repo, [_node("beef1234beef1234")])

    assert regenerated == []
    records = parse_blocks(prose["overview.md"]["requirements"])
    assert [(r.id, r.origin) for r in records] == [("R1", AUTHORED)]


def test_a_missing_claude_cli_still_migrates_what_is_on_disk(monkeypatch, tmp_path):
    repo = _setup(monkeypatch, tmp_path, LEGACY_PROSE, "01d001d001d001d0")
    monkeypatch.setattr(synth, "available", lambda: False)

    prose, regenerated = resolve_quiet(repo, [_node("beef1234beef1234")])

    assert regenerated == []
    assert [r.id for r in parse_blocks(prose["overview.md"]["requirements"])] == ["R1", "R2"]


def resolve_quiet(repo, nodes):
    return synth.resolve(repo, nodes, _evidence(), on_log=lambda *a: None)


def pytest_fail_if_called():
    raise AssertionError("synthesis must not run when the evidence hash is unchanged")
