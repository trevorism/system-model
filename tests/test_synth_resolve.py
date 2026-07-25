"""Hash gating: synthesis only fires when the evidence beneath a region actually moved."""
from pathlib import Path

import systemmodel.core.synth as synth
from systemmodel.core.evidence import Evidence
from systemmodel.core.overlay import merge_synth, synth_anchor
from systemmodel.core.schema import Level, Node

EVIDENCE = Evidence(target="demo", sections={"purpose": {"a": 1}}, shared={})


def _node(evidence_hash: str) -> Node:
    body = f"# demo\n\n{synth_anchor('purpose', evidence_hash)}\n"
    return Node(Level.L1, "overview", "overview", "overview.md",
                body=body, supports_authored=True)


def _write_prior(root: Path, evidence_hash: str, prose: str) -> None:
    body = merge_synth(f"# demo\n\n{synth_anchor('purpose', evidence_hash)}\n", {"purpose": prose})
    (root / "overview.md").write_text(body, encoding="utf-8")


def _patch(monkeypatch, tmp_path: Path, *, available=True, result="fresh prose"):
    calls: list[str] = []

    def _fake_invoke(repo, prompt, model):
        calls.append(prompt)
        return result

    monkeypatch.setattr(synth, "model_root", lambda repo: tmp_path)
    monkeypatch.setattr(synth, "available", lambda: available)
    monkeypatch.setattr(synth, "_invoke", _fake_invoke)
    return calls


def test_unchanged_evidence_reuses_prose_without_calling_the_agent(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path)
    _write_prior(tmp_path, "aaaa1111", "the preserved wording")

    prose, regenerated = resolve(tmp_path, [_node("aaaa1111")])

    assert calls == []
    assert regenerated == []
    assert prose["overview.md"]["purpose"] == "the preserved wording"


def test_moved_evidence_regenerates(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path)
    _write_prior(tmp_path, "aaaa1111", "the stale wording")

    prose, regenerated = resolve(tmp_path, [_node("bbbb2222")])

    assert len(calls) == 1
    assert regenerated == ["overview.md:purpose"]
    assert prose["overview.md"]["purpose"] == "fresh prose"


def test_missing_agent_keeps_prior_prose(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path, available=False)
    _write_prior(tmp_path, "aaaa1111", "the preserved wording")

    prose, regenerated = resolve(tmp_path, [_node("bbbb2222")])

    assert calls == []
    assert regenerated == []
    assert prose["overview.md"]["purpose"] == "the preserved wording"


def test_failed_synthesis_keeps_prior_prose(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, result=None)
    _write_prior(tmp_path, "aaaa1111", "the preserved wording")

    prose, regenerated = resolve(tmp_path, [_node("bbbb2222")])

    assert regenerated == []
    assert prose["overview.md"]["purpose"] == "the preserved wording"


def test_no_prior_model_generates_from_scratch(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path)

    prose, regenerated = resolve(tmp_path, [_node("aaaa1111")])

    assert len(calls) == 1
    assert prose["overview.md"]["purpose"] == "fresh prose"


def resolve(repo: Path, nodes: list[Node]):
    return synth.resolve(repo, nodes, EVIDENCE, on_log=lambda *_: None)
