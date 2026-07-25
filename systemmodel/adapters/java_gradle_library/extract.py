"""Extractors for the plain Java + Gradle shared-library archetype.

These repos are not services: nothing deploys them, they answer on no host, and they are consumed
by Gradle coordinate rather than over HTTP. Modelling them still matters, because they are where
the platform's hidden edges live — `Repository`, `ChannelClient` and friends hardcode the service
URLs that the ~30 repos declaring them never mention. Until now the graph asserted those hops from
a hand-written table with no way to check it against the libraries themselves.

So the two facts worth deriving are the published API surface (the types services inject) and the
hosts the library reaches on their behalf. Like the Micronaut adapter, extraction is regex/line
based over source text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from systemmodel.core.clientlibs import client_type_name, hosts_reached
from systemmodel.core.config import repo_kind_override
from systemmodel.core.evidence import Evidence
from systemmodel.core.filters import iter_files, read_text
from systemmodel.core.graph import service_graph
from systemmodel.core.overlay import synth_anchor
from systemmodel.core.schema import Level, Node

_TREVORISM_HOST = re.compile(r"https://([a-z0-9.-]+\.trevorism\.com)")
_SHARED_LIB = re.compile(r"com\.trevorism:([a-z0-9-]+)")
_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
# Package-private types are matched too: one of them holds a hardcoded service URL, and a
# host that consumers inherit is worth reporting regardless of the declaring type's visibility.
_TYPE_DECL = re.compile(
    r"^\s*(public\s+)?(?:final\s+|abstract\s+|static\s+)*"
    r"(class|interface|enum|record)\s+(\w+)",
    re.MULTILINE,
)
_ACCESSOR = re.compile(r"^(get|set|is)([A-Z]\w*)\(")
_IMPLEMENTS = re.compile(r"\bclass\s+\w+(?:<[^>]*>)?\s+(?:extends\s+[\w<>.,\s]+?\s+)?implements\s+([\w<>.,\s]+?)\s*\{")
_EXTENDS = re.compile(r"\bclass\s+\w+(?:<[^>]*>)?\s+extends\s+([\w.]+)")
_FIELD_DECL = re.compile(r"private\s+(?:(?:final|static|transient|volatile)\s+)*([\w<>.]+)\s+\w+")

# `public <T> Foo bar(...)` — modifiers, optional generic witness, return type, name, params.
_METHOD_DECL = re.compile(
    r"^\s*(?:public\s+)?(?:static\s+|final\s+|abstract\s+|default\s+|synchronized\s+)*"
    r"(?:<[^>]+>\s*)?"
    r"([\w<>\[\].,\s?]+?)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w.,\s]+)?[;{]",
    re.MULTILINE,
)
_JAVA_KEYWORDS = {"new", "return", "if", "for", "while", "switch", "catch", "this", "super"}


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _read(repo: Path, rel: str) -> str | None:
    p = repo / rel
    return read_text(p) if p.exists() else None


def _java_sources(repo: Path) -> list[Path]:
    return sorted((p for p in iter_files(repo, "src/main") if p.suffix == ".java"),
                  key=lambda p: p.as_posix())


# ------------------------------------------------------------------------- published API

@dataclass
class JavaType:
    name: str
    kind: str  # class | interface | enum | record
    package: str
    file: str
    exported: bool = True
    implements: list[str] = field(default_factory=list)
    extends: str | None = None
    methods: list[str] = field(default_factory=list)
    reaches: list[str] = field(default_factory=list)

    @property
    def supertypes(self) -> list[str]:
        return ([self.extends] if self.extends else []) + self.implements

    @property
    def properties(self) -> list[str]:
        names: list[str] = []
        for signature in self.methods:
            m = _ACCESSOR.match(signature)
            if m and m.group(2) not in names:
                names.append(m.group(2))
        return [n[0].lower() + n[1:] for n in names]

    @property
    def is_data_type(self) -> bool:
        """A carrier with nothing but accessors — its properties are the interesting part."""
        return bool(self.methods) and all(_ACCESSOR.match(s) for s in self.methods)


def _split_params(params: str) -> list[str]:
    """Split a parameter list on commas that are not inside a generic argument list."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _param_type(param: str) -> str:
    """The declared type of one parameter, dropping annotations, `final`, and the name."""
    tokens = [t for t in param.split() if not t.startswith("@") and t != "final"]
    return " ".join(tokens[:-1]) if len(tokens) > 1 else (tokens[0] if tokens else "")


def _signature(return_type: str, name: str, params: str) -> str:
    args = ", ".join(_param_type(p) for p in _split_params(params))
    return f"{name}({args}) → {' '.join(return_type.split())}"


def _public_methods(text: str, type_name: str) -> list[str]:
    """Public method signatures, in declaration order.

    An interface body has no `public` modifier on its members, so the modifier cannot be
    required; instead constructors, control-flow lookalikes and non-public members are filtered
    out by name.
    """
    is_interface = re.search(rf"\binterface\s+{re.escape(type_name)}\b", text) is not None
    methods: list[str] = []
    for m in _METHOD_DECL.finditer(text):
        return_type, name, params = m.group(1).strip(), m.group(2), m.group(3)
        if name in _JAVA_KEYWORDS or return_type in _JAVA_KEYWORDS:
            continue
        if name == type_name or return_type.endswith(("=", "return")):
            continue
        declaration = m.group(0).lstrip()
        if not is_interface and not declaration.startswith("public"):
            continue
        signature = _signature(return_type, name, params)
        if signature not in methods:
            methods.append(signature)
    return methods


def _declared_types(text: str) -> set[str]:
    """Every type this file names as an import or a field — the collaborator candidates."""
    names = {imported.rsplit(".", 1)[-1] for imported in _IMPORT.findall(text)}
    names.update(client_type_name(f) for f in _FIELD_DECL.findall(text))
    return {n for n in names if n}


def _parse_types(repo: Path) -> list[JavaType]:
    types: list[JavaType] = []
    for path in _java_sources(repo):
        text = read_text(path)
        pkg_m = _PACKAGE.search(text)
        package = pkg_m.group(1) if pkg_m else ""
        for public, kind, name in _TYPE_DECL.findall(text):
            impl_m = _IMPLEMENTS.search(text)
            implements = ([i.strip().split("<", 1)[0] for i in impl_m.group(1).split(",")]
                          if impl_m else [])
            extends_m = _EXTENDS.search(text)
            types.append(JavaType(
                name=name, kind=kind, package=package, file=_rel(repo, path),
                exported=bool(public),
                implements=[i for i in implements if i],
                extends=extends_m.group(1).rsplit(".", 1)[-1] if extends_m else None,
                methods=_public_methods(text, name),
                reaches=sorted(set(_TREVORISM_HOST.findall(text))),
            ))
    types.sort(key=lambda t: (t.package, t.name))
    return types


def _is_internal(t: JavaType) -> bool:
    """Support types a consumer never names directly (exceptions, serialization plumbing)."""
    if not t.exported:
        return True
    tail = t.package.rsplit(".", 1)[-1]
    return tail in ("exception", "deserialize", "util") or t.name.endswith("Exception")


def _own_operations(t: JavaType, by_name: dict[str, JavaType]) -> list[str]:
    """Operations this type adds beyond the ones its supertypes already list.

    `DefaultChannelClient` restating every `ChannelClient` method doubles the operations list and
    tells the reader nothing the interface row did not; an implementation that adds something is
    worth showing, but only for what it adds.
    """
    inherited: set[str] = set()
    pending = list(t.supertypes)
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        inherited.update(by_name[name].methods)
        pending.extend(by_name[name].supertypes)
    return [m for m in t.methods if m not in inherited]


# ------------------------------------------------------------------------------- wiring

def _artifact(repo: Path) -> dict:
    settings = _read(repo, "settings.gradle") or ""
    props = _read(repo, "gradle.properties") or ""
    name_m = re.search(r"rootProject\.name\s*=\s*['\"]?([\w.-]+)['\"]?", settings)
    version_m = re.search(r"(?m)^\s*version\s*=\s*(\S+)", props)
    return {
        "name": name_m.group(1) if name_m else repo.name,
        "group": "com.trevorism",
        "version": version_m.group(1) if version_m else None,
    }


def _dependencies(repo: Path) -> list[str]:
    build = _read(repo, "build.gradle") or ""
    return sorted({lib for lib in _SHARED_LIB.findall(build)
                   if not lib.endswith("-plugin") and lib != repo.name})


def _wiring(repo: Path) -> dict:
    """Outbound edges for the graph. A library claims no host — it is not addressable."""
    hosts: set[str] = set()
    declared: set[str] = set()
    for path in _java_sources(repo):
        text = read_text(path)
        hosts.update(_TREVORISM_HOST.findall(text))
        declared.update(_declared_types(text))
    dependencies = set(_dependencies(repo))
    via_libraries = hosts_reached(declared, dependencies)
    for host in hosts:
        via_libraries.pop(host, None)
    return {
        "hosts": [],
        "calls": sorted(hosts),
        "library_calls": via_libraries,
        "publishes_topics": [],
        "shared_libraries": sorted(dependencies),
    }


def _consumers(repo: Path) -> list[str]:
    return service_graph().library_consumers_of(_artifact(repo)["name"])


# ------------------------------------------------------------------------------- render

def _md_overview(repo: Path, artifact: dict, wiring: dict, types: list[JavaType],
                 consumers: list[str], evidence_hashes: dict) -> str:
    coordinate = f"{artifact['group']}:{artifact['name']}"
    identity = [f"`{coordinate}`", "shared library"]
    if artifact.get("version"):
        identity.append(f"v{artifact['version']}")
    lines = [f"# {artifact['name']}", "", " · ".join(identity), "",
             synth_anchor("purpose", evidence_hashes.get("purpose", "")), ""]

    lines += ["## Requirements", "",
              synth_anchor("requirements", evidence_hashes.get("requirements", "")), ""]

    lines += ["## Wiring", ""]
    calls = " · ".join(wiring["calls"]) or "_(nothing outbound)_"
    lines.append(f"- **calls** → {calls}")
    via_libs = wiring.get("library_calls") or {}
    if via_libs:
        reached = " · ".join(f"{host} (via `{'`, `'.join(clients)}`)"
                             for host, clients in via_libs.items())
        lines.append(f"- **calls via libraries** → {reached}")
    lines.append(f"- **declared by** → "
                 + (f"{len(consumers)} repos: {', '.join(consumers)}" if consumers
                    else "_(no repo in this container declares it)_"))
    if wiring["shared_libraries"]:
        lines.append(f"- **libs** → {', '.join(wiring['shared_libraries'])}")
    lines += ["",
              "`declared by` counts repos naming this artifact in their own `build.gradle`. A repo "
              "pulling it in transitively through another library is a real consumer too and is "
              "not counted here — treat the number as a floor, not the blast radius.", ""]

    lines += ["## Watch out", ""]
    notes = _risk_notes(wiring, types, consumers)
    lines += [f"- {note}" for note in notes] if notes else [
        "_Nothing flagged: the library reaches no platform service on a caller's behalf._"]
    lines.append("")
    return "\n".join(lines)


def _risk_notes(wiring: dict, types: list[JavaType], consumers: list[str]) -> list[str]:
    notes: list[str] = []
    by_host: dict[str, list[str]] = {}
    for t in types:
        for host in t.reaches:
            by_host.setdefault(host, []).append(t.name)
    for host in sorted(by_host):
        declared_in = ", ".join(f"`{n}`" for n in sorted(set(by_host[host])))
        notes.append(f"`{host}` is hardcoded in {declared_in} — every consumer inherits this "
                     f"dependency without naming it, so it cannot be pointed elsewhere per caller.")
    if len(consumers) >= 5:
        notes.append(f"{len(consumers)} repos declare this artifact — a breaking change to the "
                     f"published API is a platform-wide change.")
    return notes


def _root_package(types: list[JavaType]) -> str:
    packages = [t.package.split(".") for t in types if t.package]
    if not packages:
        return ""
    common = packages[0]
    for parts in packages[1:]:
        common = [a for a, b in zip(common, parts) if a == b]
    return ".".join(common)


def _md_api(artifact: dict, types: list[JavaType]) -> str:
    coordinate = f"{artifact['group']}:{artifact['name']}"
    root = _root_package(types)
    lines = ["# Published API (L2)", "",
             f"The surface a consumer gets from `{coordinate}`. Types a service injects are the "
             f"contract; support types are listed separately.", ""]
    if root:
        lines += [f"Packages are shown relative to `{root}`.", ""]

    public = [t for t in types if not _is_internal(t)]
    internal = [t for t in types if _is_internal(t)]
    by_name = {t.name: t for t in types}

    def relative(package: str) -> str:
        if not root or package == root:
            return "."
        return package[len(root) + 1:] if package.startswith(root + ".") else package

    lines += ["## Contract types", "",
              "| Type | Package | Kind | Extends / implements | Reaches |",
              "|---|---|---|---|---|"]
    for t in public:
        supertypes = ", ".join(f"`{s}`" for s in t.supertypes) or ""
        reaches = ", ".join(f"`{h}`" for h in t.reaches) or ""
        lines.append(f"| {t.name} | `{relative(t.package)}` | {t.kind} | {supertypes} | {reaches} |")
    lines.append("")

    operations = [(t, _own_operations(t, by_name)) for t in public if not t.is_data_type]
    operations = [(t, own) for t, own in operations if own]
    if operations:
        lines += ["## Operations", ""]
        for t, own in operations:
            suffix = " _(beyond its supertypes)_" if t.supertypes and len(own) < len(t.methods) else ""
            lines.append(f"- **{t.name}**{suffix}")
            for signature in own:
                lines.append(f"  - `{signature}`")
        lines.append("")

    carriers = [t for t in public if t.is_data_type]
    if carriers:
        lines += ["## Data types", ""]
        for t in carriers:
            lines.append(f"- **{t.name}** — {', '.join(t.properties)}")
        lines.append("")

    if internal:
        lines += ["## Support types", "",
                  ", ".join(f"`{t.name}`" for t in sorted(internal, key=lambda t: t.name)), ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- evidence

def build_evidence(repo: Path) -> Evidence:
    artifact = _artifact(repo)
    types = _parse_types(repo)
    wiring = dict(_wiring(repo))
    consumers = _consumers(repo)
    wiring["consumed_by"] = consumers

    shared = {
        "name": artifact["name"],
        "artifact": f"{artifact['group']}:{artifact['name']}",
        "kind": "library",
        "readme_purpose": _readme_purpose(repo),
        "wiring": wiring,
    }
    surface = {
        "contract_types": [
            {"name": t.name, "kind": t.kind, "package": t.package, "file": t.file,
             "implements": t.implements, "methods": t.methods, "reaches": t.reaches}
            for t in types if not _is_internal(t)
        ],
        "support_types": [t.name for t in types if _is_internal(t)],
        "consumers": consumers,
    }
    return Evidence(
        target=repo.name,
        sections={"purpose": {"summary": shared}, "requirements": surface},
        shared=shared,
    )


def _config_provenance(repo: Path) -> set[str]:
    """Non-source files a node is derived from, named as they are actually spelled on disk.

    `exists()` is case-insensitive on Windows, so probing both `README.md` and `Readme.md` would
    list a file that isn't there. Match against the real directory entries instead.
    """
    present = {p.name.lower(): p.name for p in repo.iterdir() if p.is_file()}
    wanted = ["readme.md", "build.gradle", "gradle.properties"]
    return {present[name] for name in wanted if name in present}


def _readme_purpose(repo: Path) -> str | None:
    readme = _read(repo, "README.md") or _read(repo, "Readme.md") or ""
    for line in readme.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "!", "[")):
            return s
    return None


def _classify(repo: Path) -> str:
    name = repo.name
    if name.startswith("template-"):
        return "template"
    if name.endswith("-tester"):
        return "tester"
    return "library"


def classify(repo: Path) -> str:
    return repo_kind_override(repo.name) or _classify(repo)


# ------------------------------------------------------------------------------- adapter

class JavaGradleLibraryAdapter:
    name = "java_gradle_library"

    def detect(self, repo: Path) -> bool:
        """A published Java library, and specifically not a Micronaut app.

        The Micronaut adapter is registered first and wins any overlap, but detection is kept
        exclusive anyway so `--adapter` and any future ordering change stay honest.
        """
        if not (repo / "src" / "main" / "java").exists():
            return False
        build = _read(repo, "build.gradle") or ""
        if "micronaut" in build.lower() or (repo / "src" / "main" / "groovy").exists():
            return False
        return any(marker in build for marker in ("java-library", "maven-publish", "java-gradle-plugin"))

    def classify(self, repo: Path) -> str:
        return classify(repo)

    def extract_evidence(self, repo: Path) -> Evidence:
        return build_evidence(repo)

    def wiring(self, repo: Path) -> dict:
        return _wiring(repo)

    def extract_overview(self, repo: Path) -> Node:
        artifact = _artifact(repo)
        types = _parse_types(repo)
        wiring = _wiring(repo)
        hashes = build_evidence(repo).hashes()
        provenance = sorted({t.file for t in types} | _config_provenance(repo))
        return Node(
            level=Level.L1, kind="overview", id="overview", path="overview.md",
            body=_md_overview(repo, artifact, wiring, types, _consumers(repo), hashes),
            derived_from=provenance, supports_authored=True,
        )

    def extract_modules(self, repo: Path) -> list[Node]:
        types = _parse_types(repo)
        return [
            Node(Level.L2, "module", "api", "modules/api.md",
                 _md_api(_artifact(repo), types),
                 derived_from=sorted({t.file for t in types})),
        ]

    def platform_signal_specs(self) -> list:
        """Libraries are excluded from service invariants (see platform.toml aggregate_kinds)."""
        return []

    def platform_signals(self, repo: Path) -> dict:
        return {}
