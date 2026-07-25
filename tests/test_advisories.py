"""Version lag is advisory in a change brief, never a violation and never acceptance."""
from pathlib import Path

from systemmodel.core.apply import build_brief
from systemmodel.core.platform import SignalSpec, aggregate, trailing_conventions
from systemmodel.core.schema import Level, Node

SPECS = {
    "micronaut.version": SignalSpec("micronaut.version", "Micronaut version (BOM)",
                                    "convention", "value", advisory=True),
    "coverage.minimum": SignalSpec("coverage.minimum", "Coverage minimum", "convention", "value"),
    "test.runtime": SignalSpec("test.runtime", "Unit test runtime", "convention", "value"),
    "security.enabled": SignalSpec("security.enabled", "Security enabled", "invariant", "bool"),
}

RECORDS = [
    ("memo", {"micronaut.version": "5.0.5", "coverage.minimum": "0.4",
              "test.runtime": "junit5", "security.enabled": True}),
    ("event", {"micronaut.version": "5.0.5", "coverage.minimum": "0.4",
               "test.runtime": "junit5", "security.enabled": True}),
    ("catalog", {"micronaut.version": "5.0.2", "coverage.minimum": "0.6",
                 "test.runtime": "junit5", "security.enabled": False}),
]


def _aggs(authored=None):
    return aggregate(RECORDS, SPECS, authored or {})


def test_trailing_repo_is_reported():
    assert trailing_conventions("catalog", _aggs()) == [
        ("Micronaut version (BOM)", "5.0.2", "5.0.5"),
    ]


def test_conforming_repo_has_nothing_to_say():
    assert trailing_conventions("memo", _aggs()) == []


def test_required_signals_are_not_advisory():
    """An authored requirement is a violation for the gate to report, not a friendly nudge."""
    aggs = _aggs({"micronaut.version": "5.0.6"})
    assert trailing_conventions("catalog", aggs) == []


def test_bool_invariants_are_never_advisory():
    labels = [label for label, _, _ in trailing_conventions("catalog", _aggs())]
    assert "Security enabled" not in labels


def test_deviating_upward_is_not_a_lag():
    """catalog's coverage minimum is stricter than the norm; nudging it down would be wrong."""
    labels = [label for label, _, _ in trailing_conventions("catalog", _aggs())]
    assert "Coverage minimum" not in labels


def test_unknown_repo_yields_nothing():
    assert trailing_conventions("nonexistent", _aggs()) == []


def _node_with_gap() -> Node:
    return Node(Level.L1, "overview", "overview", "overview.md", body="# derived\n")


def test_advisories_are_appended_but_excluded_from_acceptance(tmp_path: Path, monkeypatch):
    import systemmodel.core.apply as apply_mod
    monkeypatch.setattr(apply_mod, "model_root", lambda repo: tmp_path)
    (tmp_path / "overview.md").write_text("# desired\n", encoding="utf-8")

    brief = build_brief(tmp_path, [_node_with_gap()],
                        advisories=["**Micronaut version (BOM):** `5.0.2` — most are on `5.0.5`"])

    assert "While you're here" in brief
    assert "5.0.2" in brief
    assert "none of it affects acceptance" in brief
    assert brief.index("Acceptance:") < brief.index("While you're here")


def test_no_advisories_means_no_section(tmp_path: Path, monkeypatch):
    import systemmodel.core.apply as apply_mod
    monkeypatch.setattr(apply_mod, "model_root", lambda repo: tmp_path)
    (tmp_path / "overview.md").write_text("# desired\n", encoding="utf-8")

    brief = build_brief(tmp_path, [_node_with_gap()])
    assert "While you're here" not in brief


def test_advisories_never_manufacture_a_brief(tmp_path: Path, monkeypatch):
    """A repo that matches its spec gets no brief, however far its versions trail."""
    import systemmodel.core.apply as apply_mod
    monkeypatch.setattr(apply_mod, "model_root", lambda repo: tmp_path)
    node = _node_with_gap()
    (tmp_path / "overview.md").write_text(node.body, encoding="utf-8")

    assert build_brief(tmp_path, [node], advisories=["**Anything:** `x` — most are on `y`"]) is None
