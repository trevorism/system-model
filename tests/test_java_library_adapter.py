"""Deriving the plain Java + Gradle shared libraries, and keeping the client table honest."""
from pathlib import Path

import pytest

import systemmodel.core.graph as graph
from systemmodel.adapters.java_gradle_library.extract import (
    JavaGradleLibraryAdapter, _md_api, _own_operations, _param_type, _parse_types,
    _root_package, _split_params, _wiring,
)
from systemmodel.core.clientlibs import hosts_hardcoded_by
from systemmodel.core.locate import dev_dir

LIBRARY_BUILD = """plugins{
    id "java-library"
    id "jacoco"
}

dependencies {
    api 'com.trevorism:secure-http-utils:3.2.0'
    implementation 'com.google.code.gson:gson:2.11.0'
}
"""

REPOSITORY_JAVA = """package com.trevorism.data;

import java.util.List;

public interface Repository<T> {

    List<T> all();

    T get(String id);

    T create(T itemToCreate);
}
"""

FAST_REPOSITORY_JAVA = """package com.trevorism.data;

import com.trevorism.https.AppClientSecureHttpClient;
import com.trevorism.https.SecureHttpClient;

import java.util.List;

public class FastDatastoreRepository<T> implements Repository<T> {

    private final SecureHttpClient client;

    public List<T> all() {
        return null;
    }

    public T get(String id) {
        return null;
    }

    public T create(T itemToCreate) {
        return null;
    }

    public void refresh(final String id, Map<String, String> headers) {
    }
}
"""

REQUEST_UTILS_JAVA = """package com.trevorism.data;

final class RequestUtils {
    static final String DATASTORE_BASE_URL = "https://datastore.data.trevorism.com";
}
"""

SUBSCRIPTION_JAVA = """package com.trevorism.data.model;

public class EventSubscription {

    private String name;

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }
}
"""


def _library_repo(root: Path, *, build: str = LIBRARY_BUILD, name: str = "datastore-client") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle").write_text(f'rootProject.name="{name}"', encoding="utf-8")
    (root / "build.gradle").write_text(build, encoding="utf-8")
    (root / "gradle.properties").write_text("version=4.1.1\n", encoding="utf-8")
    pkg = root / "src/main/java/com/trevorism/data"
    pkg.mkdir(parents=True)
    (pkg / "Repository.java").write_text(REPOSITORY_JAVA, encoding="utf-8")
    (pkg / "FastDatastoreRepository.java").write_text(FAST_REPOSITORY_JAVA, encoding="utf-8")
    (pkg / "RequestUtils.java").write_text(REQUEST_UTILS_JAVA, encoding="utf-8")
    model = root / "src/main/java/com/trevorism/data/model"
    model.mkdir(parents=True)
    (model / "EventSubscription.java").write_text(SUBSCRIPTION_JAVA, encoding="utf-8")
    return root


def test_detect_matches_a_published_java_library(tmp_path: Path):
    assert JavaGradleLibraryAdapter().detect(_library_repo(tmp_path)) is True


def test_detect_rejects_a_micronaut_app(tmp_path: Path):
    repo = _library_repo(tmp_path, build='plugins { id "io.micronaut.application" version "5.0.2" }')
    assert JavaGradleLibraryAdapter().detect(repo) is False


def test_detect_rejects_a_groovy_source_tree(tmp_path: Path):
    repo = _library_repo(tmp_path)
    (repo / "src/main/groovy").mkdir(parents=True)
    assert JavaGradleLibraryAdapter().detect(repo) is False


def test_detect_rejects_a_java_tree_that_publishes_nothing(tmp_path: Path):
    repo = _library_repo(tmp_path, build='plugins { id "application" }')
    assert JavaGradleLibraryAdapter().detect(repo) is False


def test_classify_is_library(tmp_path: Path):
    assert JavaGradleLibraryAdapter().classify(_library_repo(tmp_path)) == "library"


def test_a_library_claims_no_host_so_it_cannot_own_an_edge_target(tmp_path: Path):
    wiring = _wiring(_library_repo(tmp_path))
    assert wiring["hosts"] == []


def test_wiring_reports_the_hardcoded_host_and_the_library_mediated_one(tmp_path: Path):
    wiring = _wiring(_library_repo(tmp_path))
    assert wiring["calls"] == ["datastore.data.trevorism.com"]
    assert wiring["library_calls"] == {
        "auth.trevorism.com": ["AppClientSecureHttpClient", "SecureHttpClient"],
    }
    assert wiring["shared_libraries"] == ["secure-http-utils"]


def test_package_private_type_is_support_but_still_reports_its_host(tmp_path: Path):
    types = {t.name: t for t in _parse_types(_library_repo(tmp_path))}
    assert types["RequestUtils"].exported is False
    assert types["RequestUtils"].reaches == ["datastore.data.trevorism.com"]
    body = _md_api({"group": "com.trevorism", "name": "datastore-client"},
                   list(types.values()))
    assert "`RequestUtils`" in body.split("## Support types")[1]


def test_interface_methods_are_captured_without_a_public_modifier(tmp_path: Path):
    types = {t.name: t for t in _parse_types(_library_repo(tmp_path))}
    assert types["Repository"].methods == [
        "all() → List<T>", "get(String) → T", "create(T) → T",
    ]


def test_an_implementation_lists_only_what_it_adds(tmp_path: Path):
    types = {t.name: t for t in _parse_types(_library_repo(tmp_path))}
    own = _own_operations(types["FastDatastoreRepository"], types)
    assert own == ["refresh(String, Map<String, String>) → void"]


def test_an_accessor_only_type_is_rendered_as_data_not_operations(tmp_path: Path):
    types = _parse_types(_library_repo(tmp_path))
    subscription = next(t for t in types if t.name == "EventSubscription")
    assert subscription.is_data_type is True
    assert subscription.properties == ["name"]
    body = _md_api({"group": "com.trevorism", "name": "datastore-client"}, types)
    assert "- **EventSubscription** — name" in body
    assert "getName()" not in body


def test_generic_parameters_are_not_split_on_their_inner_comma():
    assert _split_params("String url, Map<String, String> headers") == [
        "String url", "Map<String, String> headers",
    ]
    assert _param_type("final String url") == "String"
    assert _param_type("Map<String, String> headers") == "Map<String, String>"
    assert _param_type("@Nullable String id") == "String"


def test_root_package_is_the_common_prefix(tmp_path: Path):
    assert _root_package(_parse_types(_library_repo(tmp_path))) == "com.trevorism.data"


class _Stub:
    def __init__(self, table):
        self.table = table

    def wiring(self, repo: Path) -> dict:
        return self.table[repo.name]


def test_declared_artifacts_invert_into_a_library_consumer_index(monkeypatch):
    table = {
        "memo": {"hosts": ["memo.trevorism.com"], "calls": [],
                 "shared_libraries": ["datastore-client", "secure-http-utils"]},
        "list": {"hosts": ["list.data.trevorism.com"], "calls": [],
                 "shared_libraries": ["datastore-client"]},
        "datastore-client": {"hosts": [], "calls": [],
                             "shared_libraries": ["secure-http-utils", "datastore-client"]},
    }
    monkeypatch.setattr(graph.adapters, "select", lambda repo: _Stub(table))
    g = graph.build([Path(name) for name in table])

    assert g.library_consumers_of("datastore-client") == ["list", "memo"]
    assert g.library_consumers_of("secure-http-utils") == ["datastore-client", "memo"]
    assert g.library_consumers_of("nothing-uses-this") == []


# The client-library table claims a set of hosts per artifact. For the libraries actually checked
# out here that claim is verifiable, and an unverified claim is how the graph quietly goes wrong —
# so verify it. Hosts the table adds transitively (auth, reached through secure-http-utils, whose
# repo is not in the container) are expected to be absent from the library's own source.
TRANSITIVE_HOSTS = {"auth.trevorism.com"}


@pytest.mark.parametrize("artifact", ["datastore-client", "event-client"])
def test_table_hosts_agree_with_the_library_source(artifact: str):
    repo = dev_dir() / artifact
    if not repo.exists():
        pytest.skip(f"{artifact} is not checked out under {dev_dir()}")
    in_source = set(_wiring(repo)["calls"])
    claimed = set(hosts_hardcoded_by(artifact)) - TRANSITIVE_HOSTS
    assert in_source == claimed
