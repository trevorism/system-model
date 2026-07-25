"""Inverting outbound host edges into a consumer index, including library-mediated ones."""
from pathlib import Path

import systemmodel.core.graph as graph
from systemmodel.adapters.micronaut_groovy.extract import (
    _client_type_name, _library_calls, _wiring,
)
from systemmodel.core.platform import render_graph


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


def test_library_mediated_call_becomes_an_edge(monkeypatch):
    g = _build(monkeypatch, {
        "catalog": {"hosts": ["catalog.data.trevorism.com"], "calls": [],
                    "library_calls": {"datastore.data.trevorism.com": ["Repository"],
                                      "auth.trevorism.com": ["Repository"]}},
        "auth-provider": {"hosts": ["auth.trevorism.com"], "calls": []},
        "datastore": {"hosts": ["datastore.data.trevorism.com"], "calls": []},
    })
    assert g.callees_of("catalog") == ["auth-provider", "datastore"]
    assert g.callers_of("auth-provider") == ["catalog"]
    assert g.mediators_of("catalog", "datastore") == ["Repository"]


def test_direct_url_wins_over_library_attribution(monkeypatch):
    g = _build(monkeypatch, {
        "memo": {"hosts": ["memo.trevorism.com"], "calls": ["auth.trevorism.com"],
                 "library_calls": {"auth.trevorism.com": ["SecureHttpClient"]}},
        "auth-provider": {"hosts": ["auth.trevorism.com"], "calls": []},
    })
    assert g.callees_of("memo") == ["auth-provider"]
    assert g.callers_of("auth-provider") == ["memo"]
    assert g.mediators_of("memo", "auth-provider") == []


def test_library_call_to_own_host_is_not_an_edge(monkeypatch):
    g = _build(monkeypatch, {
        "datastore": {"hosts": ["datastore.data.trevorism.com"], "calls": [],
                      "library_calls": {"datastore.data.trevorism.com": ["Repository"]}},
    })
    assert g.callees_of("datastore") == []
    assert g.mediated_by == {}


def test_library_call_to_unknown_host_produces_no_edge(monkeypatch):
    g = _build(monkeypatch, {
        "memo": {"hosts": ["memo.trevorism.com"], "calls": [],
                 "library_calls": {"nowhere.trevorism.com": ["MysteryClient"]}},
    })
    assert g.callees_of("memo") == []
    assert g.mediated_by == {}


def test_mediators_merge_when_several_types_reach_one_target(monkeypatch):
    g = _build(monkeypatch, {
        "monitor": {"hosts": ["monitor.testing.trevorism.com"], "calls": [],
                    "library_calls": {"auth.trevorism.com": ["ScheduleService", "Repository"]}},
        "auth-provider": {"hosts": ["auth.trevorism.com"], "calls": []},
    })
    assert g.mediators_of("monitor", "auth-provider") == ["Repository", "ScheduleService"]


def test_wiring_without_library_calls_still_builds(monkeypatch):
    g = _build(monkeypatch, {
        "memo": {"hosts": ["memo.trevorism.com"], "calls": ["auth.trevorism.com"]},
        "auth-provider": {"hosts": ["auth.trevorism.com"], "calls": []},
    })
    assert g.callees_of("memo") == ["auth-provider"]
    assert g.mediated_by == {}


def test_consumers_are_sorted_and_deduped(monkeypatch):
    g = _build(monkeypatch, {
        "hub": {"hosts": ["hub.trevorism.com"], "calls": []},
        "zeta": {"hosts": ["zeta.trevorism.com"], "calls": ["hub.trevorism.com"]},
        "alpha": {"hosts": ["alpha.trevorism.com"],
                  "calls": ["hub.trevorism.com", "hub.trevorism.com"]},
    })
    assert g.callers_of("hub") == ["alpha", "zeta"]


CATALOG_CONTROLLER = """package com.trevorism.data.controller

import com.trevorism.data.PingingDatastoreRepository
import com.trevorism.data.Repository
import io.micronaut.http.annotation.Controller

@Controller("/catalog")
class CatalogController {

    private Repository<DataCatalog> service = new PingingDatastoreRepository<>(DataCatalog)
}
"""


def _fixture_repo(tmp_path: Path, build_gradle: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "settings.gradle").write_text("rootProject.name = 'catalog'\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text(build_gradle, encoding="utf-8")
    ctrl = tmp_path / "src/main/groovy/com/trevorism/data/controller"
    ctrl.mkdir(parents=True)
    (ctrl / "CatalogController.groovy").write_text(CATALOG_CONTROLLER, encoding="utf-8")
    return tmp_path


def test_client_type_name_strips_generics_and_modifiers():
    assert _client_type_name("Repository<App>") == "Repository"
    assert _client_type_name("private final ChannelClient") == "ChannelClient"
    assert _client_type_name("SecureHttpClient") == "SecureHttpClient"
    assert _client_type_name("") == ""


def test_library_calls_need_the_providing_artifact(tmp_path: Path):
    declared = "implementation 'com.trevorism:datastore-client:4.1.0'"
    with_client = _fixture_repo(tmp_path / "with", declared)
    without_client = _fixture_repo(tmp_path / "without", "implementation 'io.micronaut:micronaut-http'")

    assert _library_calls(with_client, {"datastore-client"}) == {
        "auth.trevorism.com": ["Repository"],
        "datastore.data.trevorism.com": ["Repository"],
    }
    assert _library_calls(without_client, set()) == {}


def test_wiring_reports_library_calls_for_a_repo_with_no_literal_urls(tmp_path: Path):
    repo = _fixture_repo(tmp_path, "implementation 'com.trevorism:datastore-client:4.1.0'")
    wiring = _wiring(repo)
    assert wiring["calls"] == []
    assert sorted(wiring["library_calls"]) == ["auth.trevorism.com", "datastore.data.trevorism.com"]


def test_render_graph_tags_library_mediated_edges(monkeypatch):
    g = _build(monkeypatch, {
        "catalog": {"hosts": ["catalog.data.trevorism.com"], "calls": ["datastore.trevorism.com"],
                    "library_calls": {"auth.trevorism.com": ["Repository"]}},
        "auth-provider": {"hosts": ["auth.trevorism.com"], "calls": []},
        "datastore": {"hosts": ["datastore.trevorism.com"], "calls": []},
    })
    body = render_graph(g, ["catalog", "auth-provider", "datastore"]).body
    assert "- calls → auth-provider (via `Repository`), datastore\n" in body
