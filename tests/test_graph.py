"""Inverting outbound host edges into a consumer index."""
from pathlib import Path

import systemmodel.core.graph as graph


class _Stub:
    def __init__(self, table):
        self.table = table

    def wiring(self, repo: Path) -> dict:
        return self.table[repo.name]


def _build(monkeypatch, table):
    monkeypatch.setattr(graph.adapters, "select", lambda repo: _Stub(table))
    return graph.build([Path(name) for name in table])


def test_edges_resolve_through_host_aliases(monkeypatch):
    g = _build(monkeypatch, {
        "memo": {"hosts": ["memo.trevorism.com"],
                 "calls": ["auth.trevorism.com", "bucket.data.trevorism.com"]},
        "auth-provider": {"hosts": ["auth.trevorism.com"], "calls": []},
        "bucket": {"hosts": ["bucket.data.trevorism.com", "bucket.trevorism.com"], "calls": []},
    })
    assert g.callees_of("memo") == ["auth-provider", "bucket"]
    assert g.callers_of("auth-provider") == ["memo"]
    assert g.callers_of("bucket") == ["memo"]


def test_unknown_host_produces_no_edge(monkeypatch):
    g = _build(monkeypatch, {
        "memo": {"hosts": ["memo.trevorism.com"], "calls": ["stripe.com"]},
    })
    assert g.callees_of("memo") == []
    assert g.callers_of("memo") == []


def test_self_reference_is_not_an_edge(monkeypatch):
    g = _build(monkeypatch, {
        "event": {"hosts": ["event.data.trevorism.com"], "calls": ["event.data.trevorism.com"]},
    })
    assert g.callees_of("event") == []


def test_ambiguous_alias_is_dropped_rather_than_guessed(monkeypatch):
    g = _build(monkeypatch, {
        "one": {"hosts": ["shared.trevorism.com"], "calls": []},
        "two": {"hosts": ["shared.trevorism.com"], "calls": []},
        "caller": {"hosts": ["caller.trevorism.com"], "calls": ["shared.trevorism.com"]},
    })
    assert "shared.trevorism.com" not in g.host_to_repo
    assert g.callees_of("caller") == []


def test_consumers_are_sorted_and_deduped(monkeypatch):
    g = _build(monkeypatch, {
        "hub": {"hosts": ["hub.trevorism.com"], "calls": []},
        "zeta": {"hosts": ["zeta.trevorism.com"], "calls": ["hub.trevorism.com"]},
        "alpha": {"hosts": ["alpha.trevorism.com"],
                  "calls": ["hub.trevorism.com", "hub.trevorism.com"]},
    })
    assert g.callers_of("hub") == ["alpha", "zeta"]
