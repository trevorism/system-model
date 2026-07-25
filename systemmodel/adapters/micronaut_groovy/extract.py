"""Extractors for the Micronaut + Groovy backend archetype.

Code is truth: every fact here is derived by reading source/config as text. At the
annotation/config granularity these facts live at, regex/line parsing is sufficient and
keeps the adapter easy to extend. Each extractor returns pre-rendered Node(s).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from systemmodel.core.config import aggregate_kinds, repo_kind_override
from systemmodel.core.evidence import Evidence
from systemmodel.core.filters import iter_files, read_text
from systemmodel.core.overlay import synth_anchor
from systemmodel.core.platform import SignalSpec
from systemmodel.core.schema import Level, Node

# Platform-scoped signals: facts that are properties of the platform, not any one repo.
# Aggregated across repos into the L0 model; each repo reports its own value.
PLATFORM_SIGNAL_SPECS = [
    SignalSpec("security.enabled", "Micronaut security enabled", "invariant", "bool"),
    SignalSpec("https.secure_always", "HTTPS enforced (App Engine secure:always)", "invariant", "bool"),
    SignalSpec("https.redirect", "HTTP→HTTPS redirect", "invariant", "bool"),
    SignalSpec("coverage.gate", "Coverage gate wired into build", "invariant", "bool"),
    SignalSpec("ping.present", "Liveness /ping endpoint", "invariant", "bool"),
    SignalSpec("micronaut.version_aligned", "Micronaut BOM/plugin versions aligned", "invariant", "bool"),
    SignalSpec("jdk", "JDK version", "convention", "value"),
    SignalSpec("micronaut.version", "Micronaut version (BOM)", "convention", "value"),
    SignalSpec("micronaut.plugin.version", "Micronaut version (application plugin)", "convention", "value"),
    SignalSpec("test.runtime", "Unit test runtime", "convention", "value"),
    SignalSpec("coverage.minimum", "Coverage minimum", "convention", "value"),
]

HTTP_ANNOTATION = re.compile(r"@(Get|Post|Put|Delete|Patch|Head|Options)\b(?:\((.*)\))?")
CLASS_DECL = re.compile(r"\bclass\s+(\w+)")
IFACE_DECL = re.compile(r"\binterface\s+(\w+)")
IMPL_DECL = re.compile(r"\bclass\s+(\w+)\s+implements\s+(\w+)")
METHOD_CALL = re.compile(r"(\w+)\s*\(")

# Groovy keywords that a naive `Type name` field regex would otherwise mistake for fields.
_FIELD_STOPWORDS = {
    "return", "import", "package", "new", "assert", "throw", "if", "else",
    "for", "while", "class", "interface", "enum", "extends", "implements",
    "this", "super", "def",
}


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _first_string(text: str) -> str | None:
    m = re.search(r'"([^"]*)"', text)
    return m.group(1) if m else None


def _read(repo: Path, rel: str) -> str | None:
    p = repo / rel
    return read_text(p) if p.exists() else None


# The Micronaut version is declared in two independent places that can drift apart: the
# dependency BOM (`micronautVersion` in gradle.properties) and the Gradle application plugin
# (`id("io.micronaut.application") version "X"` in build.gradle). Reading only the BOM
# under-reports repos whose plugin lags (e.g. bom=5.0.2 but plugin=5.0.0).
_MN_PLUGIN_RE = re.compile(r'io\.micronaut\.application["\')\s]*version\s*["\']([^"\']+)["\']')


def _micronaut_plugin_version(build: str) -> str | None:
    m = _MN_PLUGIN_RE.search(build)
    return m.group(1) if m else None


def _yaml_direct_child(text: str, parent: str, child: str) -> str | None:
    """Value of a *direct* child key under `parent:` in indentation-based YAML.

    Indentation-aware so a nested key (e.g. `security.token.jwt.enabled`) is not
    mistaken for the direct child (`security.enabled`).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^(\s*){re.escape(parent)}:\s*$", line)
        if not m:
            continue
        parent_indent = len(m.group(1))
        child_indent: int | None = None
        for follow in lines[i + 1:]:
            if not follow.strip():
                continue
            indent = len(follow) - len(follow.lstrip())
            if indent <= parent_indent:
                break  # dedent → parent block ended
            if child_indent is None:
                child_indent = indent
            if indent == child_indent:
                cm = re.match(rf"^\s*{re.escape(child)}:\s*(\S+)", follow)
                if cm:
                    return cm.group(1)
        # child not in this block — keep scanning; another `parent:` block may have it
    return None


def _yaml_bool(value: str | None) -> bool:
    """Interpret a raw YAML scalar as a boolean (true/yes, any case, quotes tolerated)."""
    if value is None:
        return False
    return value.strip().strip('"\'').lower() in ("true", "yes")


# --------------------------------------------------------------------------- controllers

@dataclass
class Endpoint:
    http: str
    route: str
    handler: str
    role: str | None = None
    permissions: str | None = None
    allow_internal: bool = False
    secured: bool = False
    summary: str | None = None  # @Operation(summary=...) — human description, if present
    tag: str | None = None      # @Tag(name=...) — grouping, if present


@dataclass
class Controller:
    name: str
    prefix: str
    file: str
    endpoints: list[Endpoint] = field(default_factory=list)
    injects: list[tuple[str, str]] = field(default_factory=list)
    constructor_params: list[str] = field(default_factory=list)


def _join_route(prefix: str, value: str) -> str:
    combined = (prefix or "/").rstrip("/") + "/" + (value or "").lstrip("/")
    combined = re.sub(r"/+", "/", combined)
    if len(combined) > 1:
        combined = combined.rstrip("/")
    return combined or "/"


def _parse_secure(argstr: str) -> tuple[str | None, str | None, bool]:
    role = None
    m = re.search(r"Roles\.(\w+)", argstr)
    if m:
        role = m.group(1).lower()
    perms = None
    m = re.search(r"permissions\s*=\s*([^,)]+)", argstr)
    if m:
        raw = m.group(1).strip().strip('"').strip("'")
        perms = raw.replace("Permissions.", "")
    allow = bool(re.search(r"allowInternal\s*=\s*true", argstr))
    return role, perms, allow


def _annotation_value(argstr: str | None) -> str:
    if not argstr:
        return ""
    m = re.search(r'value\s*=\s*"([^"]*)"', argstr)
    if m:
        return m.group(1)
    # Positional route only when the FIRST argument is a bare string literal
    # (`@Get("/x")`); a keyword arg like `produces = "application/json"` has no route.
    m = re.match(r'\s*"([^"]*)"', argstr)
    return m.group(1) if m else ""


def _parse_controller(repo: Path, path: Path) -> Controller | None:
    text = read_text(path)
    if "@Controller" not in text:
        return None
    class_m = CLASS_DECL.search(text)
    name = class_m.group(1) if class_m else path.stem
    prefix_m = re.search(r"@Controller\s*\(\s*(.*?)\)", text, re.DOTALL)
    prefix = _first_string(prefix_m.group(1)) if prefix_m else "/"
    prefix = prefix or "/"

    ctrl = Controller(name=name, prefix=prefix, file=_rel(repo, path))

    # constructor params (constructor injection): `Name(...) {`
    for m in re.finditer(rf"\b{re.escape(name)}\s*\(([^)]*)\)\s*\{{", text):
        params = [p.strip() for p in m.group(1).split(",") if p.strip()]
        ctrl.constructor_params = [p.split()[0] for p in params if " " in p or p]
        break

    pending_http: tuple[str, str] | None = None
    pending_secure: tuple[str | None, str | None, bool] | None = None
    class_secure: tuple[str | None, str | None, bool] | None = None
    pending_summary: str | None = None
    pending_tag: str | None = None
    seen_class = False
    prev_inject = False

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if not seen_class and re.match(r"(?:\w+\s+)*class\s+\w+", s):
            seen_class = True
        if s.startswith("@Inject"):
            prev_inject = True
            continue
        if s.startswith("@Secure"):
            # @Secure before `class` is a class-level default inherited by every
            # endpoint that lacks its own @Secure; after `class` it is method-level.
            if seen_class:
                pending_secure = _parse_secure(s)
            else:
                class_secure = _parse_secure(s)
            continue
        if s.startswith("@Operation"):
            m = re.search(r'summary\s*=\s*"([^"]*)"', s)
            if m:
                pending_summary = m.group(1)
            continue
        if s.startswith("@Tag"):
            m = re.search(r'name\s*=\s*"([^"]*)"', s)
            if m:
                pending_tag = m.group(1)
            continue
        http_m = HTTP_ANNOTATION.match(s)
        if http_m:
            pending_http = (http_m.group(1).upper(), _annotation_value(http_m.group(2)))
            continue
        if s.startswith("@"):
            continue
        if prev_inject:
            fm = re.match(r"([\w<>,.\[\] ]+?)\s+(\w+)\s*$", s)
            if fm:
                ctrl.injects.append((fm.group(1).strip(), fm.group(2)))
            prev_inject = False
            # fall through in case this line is also a method (unlikely)
        if pending_http:
            call = METHOD_CALL.search(s)
            if call and not s.startswith("//"):
                effective = pending_secure or class_secure
                role, perms, allow = effective or (None, None, False)
                ctrl.endpoints.append(
                    Endpoint(
                        http=pending_http[0],
                        route=_join_route(prefix, pending_http[1]),
                        handler=call.group(1),
                        role=role,
                        permissions=perms,
                        allow_internal=allow,
                        secured=effective is not None,
                        summary=pending_summary,
                        tag=pending_tag,
                    )
                )
                pending_http = None
                pending_secure = None
                pending_summary = None
                pending_tag = None
    return ctrl


def _all_controllers(repo: Path) -> list[Controller]:
    controllers: list[Controller] = []
    for path in iter_files(repo, "src/main"):
        if path.suffix == ".groovy" and path.stem.endswith("Controller"):
            ctrl = _parse_controller(repo, path)
            if ctrl:
                controllers.append(ctrl)
    controllers.sort(key=lambda c: c.name)
    return controllers


# ------------------------------------------------------------------------------ services

@dataclass
class Service:
    interface: str | None
    impl: str | None
    file: str
    singleton: bool
    collaborators: list[str] = field(default_factory=list)


def _collaborators(text: str) -> list[str]:
    found: list[str] = []
    for fm in re.finditer(r"private\s+([\w<>]+)\s+\w+", text):
        t = fm.group(1)
        if any(t.endswith(sfx) or t.startswith(sfx) for sfx in ("Client", "Service", "Repository")):
            found.append(t)
    return sorted(set(found))


def _is_service_file(path: Path) -> bool:
    posix = path.as_posix()
    return path.suffix == ".groovy" and (
        "/service/" in posix or path.stem.endswith("Service") or path.stem.endswith("Client")
    )


def _parse_services(repo: Path) -> list[Service]:
    """Pair each `Default<X> implements X` impl with its interface into one row."""
    interfaces: dict[str, str] = {}
    impls: list[Service] = []
    paired: set[str] = set()
    for path in iter_files(repo, "src/main"):
        if not _is_service_file(path):
            continue
        text = read_text(path)
        impl_m = IMPL_DECL.search(text)
        iface_m = IFACE_DECL.search(text)
        if impl_m:
            impls.append(Service(
                interface=impl_m.group(2), impl=impl_m.group(1), file=_rel(repo, path),
                singleton="Singleton" in text, collaborators=_collaborators(text),
            ))
            paired.add(impl_m.group(2))
        elif iface_m:
            interfaces[iface_m.group(1)] = _rel(repo, path)
    services = list(impls)
    for name, filerel in interfaces.items():
        if name not in paired:
            services.append(Service(interface=name, impl=None, file=filerel,
                                    singleton=False, collaborators=[]))
    services.sort(key=lambda s: (s.interface or s.impl or ""))
    return services


# -------------------------------------------------------------------------------- domain

@dataclass
class DomainType:
    name: str
    kind: str  # class | enum
    file: str
    fields: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)


def _parse_domain(repo: Path) -> list[DomainType]:
    types: list[DomainType] = []
    for path in iter_files(repo, "src/main"):
        if path.suffix != ".groovy" or "/model/" not in path.as_posix():
            continue
        text = read_text(path)
        enum_m = re.search(r"\benum\s+(\w+)", text)
        if enum_m:
            values: list[str] = []
            vm = re.search(r"\benum\s+\w+[^{]*\{(.*?)(?:\n\s*\w+\s*\(|\})", text, re.DOTALL)
            if vm:
                for tok in re.findall(r"\b([A-Z][A-Z0-9_]+)\b", vm.group(1)):
                    if tok not in values:
                        values.append(tok)
            types.append(DomainType(enum_m.group(1), "enum", _rel(repo, path), values=values))
            continue
        class_m = CLASS_DECL.search(text)
        if not class_m:
            continue
        fields: list[str] = []
        for line in text.splitlines():
            fm = re.match(
                r"\s*(?:private\s+|public\s+|protected\s+|final\s+|static\s+)*"
                r"([A-Za-z_][\w<>,.\[\]]*)\s+([a-z]\w*)\s*(?:=.*)?$",
                line,
            )
            if not fm:
                continue
            ftype, fname = fm.group(1), fm.group(2)
            if fname == "log" or ftype in _FIELD_STOPWORDS:
                continue
            fields.append(f"{fname}: {ftype}")
        types.append(DomainType(class_m.group(1), "class", _rel(repo, path), fields=fields))
    types.sort(key=lambda t: t.name)
    return types


# ------------------------------------------------------------------------------ L1 facts

def _service_facts(repo: Path) -> dict:
    settings = _read(repo, "settings.gradle") or ""
    readme = _read(repo, "README.md") or ""
    app_groovy = ""
    for path in iter_files(repo, "src/main"):
        if path.name == "Application.groovy":
            app_groovy = read_text(path)
            break
    build = _read(repo, "build.gradle") or ""
    app_yaml = _read(repo, "src/main/appengine/app.yaml") or ""
    application_yml = _read(repo, "src/main/resources/application.yml") or ""
    deploy_yml = _read(repo, ".github/workflows/deploy.yml") or ""

    name_m = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", settings)
    app_name_m = re.search(r"application:\s*\n\s*name:\s*(\S+)", application_yml)
    name = (name_m.group(1) if name_m else None) or (app_name_m.group(1) if app_name_m else repo.name)

    host_m = re.search(r"Deployed to\s+(?:\[[^\]]*\]\()?(https?://[^\s)]+)", readme)
    host = host_m.group(1) if host_m else None
    category = None
    app_label = None
    if host:
        h = host.split("://", 1)[-1].strip("/")
        labels = h.split(".")
        if h.endswith("trevorism.com") and len(labels) >= 3:
            sub = labels[:-2]  # drop trevorism.com
            app_label = sub[0]
            category = sub[1] if len(sub) > 1 else None

    ping = None
    for ctrl in _all_controllers(repo):
        for ep in ctrl.endpoints:
            if ep.route.endswith("/ping"):
                ping = f"{ep.http} {ep.route}"
    # OpenAPI version + description
    oapi_ver = None
    oapi_desc = None
    oapi_block = re.search(r"@OpenAPIDefinition\s*\((.*?)\)\s*(?:@|class)", app_groovy, re.DOTALL)
    scope = oapi_block.group(1) if oapi_block else app_groovy
    vm = re.search(r'version\s*=\s*"([^"]+)"', scope)
    if vm:
        oapi_ver = vm.group(1)
    dm = re.search(r'description\s*=\s*"([^"]+)"', scope)
    if dm:
        oapi_desc = dm.group(1)

    # App Engine version (build.gradle deploy block) + project id
    ae_ver = None
    ae_block = re.search(r"deploy\s*\{(.*?)\}", build, re.DOTALL)
    if ae_block:
        m = re.search(r'version\s*=\s*"([^"]+)"', ae_block.group(1))
        if m:
            ae_ver = m.group(1)
    proj_m = re.search(r'projectId\s*=\s*"([^"]+)"', build)
    gcp_project = proj_m.group(1) if proj_m else None

    # deploy.yml version + jdk
    # `(?<![\w-])` so `java-version:` / `app_version:` don't masquerade as the app version;
    # `[0-9.\-]*` so a single-digit version (e.g. `2`) still matches.
    dep_ver_m = re.search(r"(?<![\w-])version:\s*['\"]?([0-9][0-9.\-]*)['\"]?", deploy_yml)
    deploy_ver = dep_ver_m.group(1) if dep_ver_m else None
    jdk_m = re.search(r"JDK_VERSION:\s*(\d+)", deploy_yml)
    jdk = jdk_m.group(1) if jdk_m else None

    runtime_m = re.search(r"runtime:\s*(\S+)", app_yaml)
    runtime = runtime_m.group(1) if runtime_m else None

    purpose = None
    for line in readme.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("!") and not s.lower().startswith("deployed"):
            purpose = s
            break

    # version drift: normalize dashes to dots
    def norm(v):
        return v.replace("-", ".") if v else v

    versions = {
        "@OpenAPIDefinition": oapi_ver,
        "build.gradle appengine": ae_ver,
        "deploy.yml": deploy_ver,
    }
    present = {k: v for k, v in versions.items() if v}
    distinct = {norm(v) for v in present.values()}
    drift = len(distinct) > 1

    return {
        "name": name,
        "host": host,
        "app_label": app_label,
        "category": category,
        "ping": ping,
        "purpose": purpose or oapi_desc,
        "oapi_desc": oapi_desc,
        "gcp_project": gcp_project,
        "runtime": runtime,
        "jdk": jdk,
        "versions": present,
        "drift": drift,
    }


# --------------------------------------------------------------------------- capabilities

# The end-user altitude: what people and other services can *do* with this service, synthesized
# deterministically from the HTTP surface (route + verb), the security matrix (actor), and the
# collaborators of the services each controller injects (outcome). Diff-stable like every other
# derived doc; a human/agent can add narrative intent in the authored overlay (see core/overlay).

_ROLE_ACTOR = {
    "user": "an authenticated app",
    "admin": "an admin",
    "system": "a system caller",
    "internal": "an internal caller",
    "tenant_admin": "a tenant admin",
}
_ACTION = {
    "GET": "view", "POST": "create", "PUT": "update", "PATCH": "update",
    "DELETE": "remove", "HEAD": "check", "OPTIONS": "inspect",
}
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Endpoints that are infrastructure, not user capabilities.
_INFRA_HANDLERS = {"ping", "version", "help", "index"}


@dataclass
class Capability:
    id: str
    resource: str
    story: str
    summary: str | None
    endpoint: Endpoint
    actor: str
    secured: bool
    mutating: bool
    source_files: list[str] = field(default_factory=list)

    @property
    def public_mutating(self) -> bool:
        return self.mutating and not self.secured


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _actor(ep: Endpoint) -> str:
    if not ep.secured:
        return "anyone (public)"
    return _ROLE_ACTOR.get(ep.role or "", "an authenticated caller")


def _resource_of(prefix: str, route: str) -> str:
    """The resource a route acts on: the controller prefix's first segment (else the route's)."""
    for candidate in (prefix, route):
        seg = (candidate or "").strip("/").split("/")
        if seg and seg[0] and not seg[0].startswith("{"):
            return seg[0]
    return "root"


def _outcome(collaborators: list[str]) -> str:
    """Map a controller's downstream collaborator *fields* to a human outcome clause.

    Deliberately conservative: only signals that reliably mean an observable side effect count.
    A persistence collaborator ⇒ "stored"; an event-channel collaborator ⇒ "published as an
    event". We do NOT infer from a service's *class name* (e.g. `PubSub*` names the transport a
    service is built on, not that each operation emits an event) — that over-claims.
    """
    verbs: list[str] = []
    joined = " ".join(collaborators)
    if re.search(r"Repository|Datastore", joined):
        verbs.append("stored")
    if re.search(r"ChannelClient|EventProducer|EventPublisher", joined):
        verbs.append("published as an event")
    return (" and it is " + " and ".join(verbs)) if verbs else ""


def _action_verb(ep: Endpoint, outcome: str) -> str:
    if ep.http == "POST" and "published as an event" in outcome:
        return "submit"
    if ep.http == "GET" and re.search(r"all|list", ep.handler, re.IGNORECASE):
        return "list"
    return _ACTION.get(ep.http, ep.http.lower())


def _service_index(services: list[Service]) -> dict[str, Service]:
    """Index services by both their interface and implementation names, for dep resolution."""
    index: dict[str, Service] = {}
    for svc in services:
        for key in (svc.interface, svc.impl):
            if key:
                index[key] = svc
    return index


def _extract_capabilities(repo: Path) -> list[Capability]:
    controllers = _all_controllers(repo)
    services = _parse_services(repo)
    index = _service_index(services)
    caps: list[Capability] = []
    for ctrl in controllers:
        deps = [t for t, _ in ctrl.injects] + ctrl.constructor_params
        collaborators: list[str] = []
        dep_files: list[str] = []
        for dep in deps:
            svc = index.get(dep)
            if svc:
                collaborators += svc.collaborators
                dep_files.append(svc.file)
        outcome = _outcome(collaborators)
        for ep in ctrl.endpoints:
            handler = ep.handler
            resource = _resource_of(ctrl.prefix, ep.route)
            if handler in _INFRA_HANDLERS or ep.route in ("/", "/ping", "/version", "/help"):
                continue
            actor = _actor(ep)
            verb = _action_verb(ep, outcome)
            obj = f"{_article(resource)} {resource}"
            story = f"As {actor}, I can {verb} {obj}{outcome}."
            caps.append(Capability(
                id=f"{resource}.{handler}", resource=resource, story=story,
                summary=ep.summary, endpoint=ep, actor=actor, secured=ep.secured,
                mutating=ep.http in _MUTATING,
                source_files=sorted({ctrl.file, *dep_files}),
            ))
    caps.sort(key=lambda c: (c.resource, c.id))
    return caps


def _md_capabilities(caps: list[Capability], f: dict) -> str:
    lines = ["# Capabilities (L1)", "",
             "What this service lets people and other services **do** — user stories synthesized "
             "from its HTTP surface, security, and collaborators. The technical detail each story "
             "rests on is in `modules/controllers.md`.", ""]
    if f.get("purpose"):
        lines += [f["purpose"], ""]

    exposed = [c for c in caps if c.public_mutating]
    if exposed:
        lines += ["## Exposure", "",
                  "Public **write** capabilities (no `@Secure`) — verify each is intended:", ""]
        for c in exposed:
            lines.append(f"- `{c.endpoint.http} {c.endpoint.route}` — {c.story.rstrip('.')} "
                         f"(`{c.endpoint.handler}`)")
        lines.append("")

    lines += ["## Capabilities", ""]
    if not caps:
        lines += ["_No end-user capabilities derived (no annotated HTTP endpoints)._", ""]
        return "\n".join(lines)

    resource = None
    for c in caps:
        if c.resource != resource:
            resource = c.resource
            lines += [f"### {resource}", ""]
        lines.append(f"#### {c.story} <!-- cap:{c.id} -->")
        if c.summary:
            lines.append(f"_{c.summary}_")
        lines.append(f"↳ `{c.endpoint.http} {c.endpoint.route}` · `{c.endpoint.handler}`")
        lines.append("")
        # Authored overlay slot — human/agent intent preserved across re-derivation.
        lines += [f"<!-- intent:{c.id} -->", "> intent: _(unspecified)_", "<!-- /intent -->", ""]
    return "\n".join(lines)


_TREVORISM_HOST = re.compile(r"https://([a-z0-9.-]+\.trevorism\.com)")
_EVENT_TOPIC = re.compile(r"/event/([A-Za-z][A-Za-z0-9_-]*)")
_SHARED_LIB = re.compile(r"com\.trevorism:([a-z0-9-]+)")

_PUBLIC_BY_DESIGN_SEGMENTS = {
    "login", "logout", "google", "microsoft", "oauth", "callback", "refresh",
    "forgot", "reset", "register", "token", "ping", "help", "version",
    "authwarmup", "warmup", "webhook", "swagger", "openapi",
}
_PUBLIC_BY_DESIGN_HANDLERS = {
    "ping", "version", "help", "index", "root", "callback", "warmup", "authwarmup",
}


def _is_public_by_design(route: str, handler: str) -> bool:
    if handler.lower() in _PUBLIC_BY_DESIGN_HANDLERS:
        return True
    segments = {s.lower() for s in route.strip("/").split("/") if s and not s.startswith("{")}
    if segments & _PUBLIC_BY_DESIGN_SEGMENTS:
        return True
    return segments <= {"api"}


def _wiring(repo: Path) -> dict:
    own_host = _service_facts(repo).get("host") or ""
    hosts: set[str] = set()
    topics: set[str] = set()
    for path in iter_files(repo, "src/main"):
        text = read_text(path)
        if not text:
            continue
        hosts.update(_TREVORISM_HOST.findall(text))
        topics.update(_EVENT_TOPIC.findall(text))
    hosts.discard(own_host.replace("https://", "").rstrip("/"))
    build = _read(repo, "build.gradle") or ""
    libs = {lib for lib in _SHARED_LIB.findall(build) if not lib.endswith("-plugin")}
    return {
        "calls": sorted(hosts),
        "publishes_topics": sorted(topics),
        "shared_libraries": sorted(libs),
    }


def _risk_notes(repo: Path) -> list[str]:
    notes: list[str] = []
    exposed: list[tuple[str, str]] = []
    for ctrl in _all_controllers(repo):
        for ep in ctrl.endpoints:
            if ep.secured or ep.http not in _MUTATING:
                continue
            if _is_public_by_design(ep.route, ep.handler):
                continue
            exposed.append((f"{ep.http} {ep.route}", ctrl.name))
    for route, cname in exposed:
        notes.append(f"`{route}` — unauthenticated write, and not an auth/session flow ({cname}).")
    if (repo / "src/main/resources/secrets.properties").exists():
        notes.append("`secrets.properties` present under `src/main/resources` — confirm it is gitignored.")
    return notes


def _md_overview(repo: Path, f: dict, wiring: dict, risks: list[str], evidence_hashes: dict) -> str:
    title = f"# {f['name']}"
    if f.get("app_label") and f["app_label"] != f["name"]:
        title += f" — {f['app_label']}"
    identity = [f"`{f['name']}`"]
    if f.get("host"):
        identity.append(f["host"])
    if f.get("gcp_project"):
        identity.append(f"`{f['gcp_project']}`")
    if f.get("ping"):
        identity.append(f"liveness `{f['ping']}`")
    lines = [title, "", " · ".join(identity), "",
             synth_anchor("purpose", evidence_hashes.get("purpose", "")), ""]

    lines += ["## Requirements", "",
              synth_anchor("requirements", evidence_hashes.get("requirements", "")), ""]

    lines += ["## Wiring", ""]
    calls = " · ".join(wiring["calls"]) or "_(nothing outbound)_"
    lines.append(f"- **calls** → {calls}")
    if wiring["publishes_topics"]:
        lines.append(f"- **publishes** → {' · '.join(wiring['publishes_topics'])}")
    if wiring["shared_libraries"]:
        lines.append(f"- **libs** → {', '.join(wiring['shared_libraries'])}")
    lines.append("")

    lines += ["## Watch out", ""]
    if risks:
        lines += [f"- {note}" for note in risks]
    else:
        lines.append("_Nothing flagged: no unauthenticated writes outside auth flows._")
    lines += ["", f"<!-- intent:{f['name']} -->", "> intent: _(unspecified)_", "<!-- /intent -->", ""]
    return "\n".join(lines)


def build_evidence(repo: Path) -> Evidence:
    facts = _service_facts(repo)
    controllers = _all_controllers(repo)
    services = _parse_services(repo)
    domain = _parse_domain(repo)
    caps = _extract_capabilities(repo)
    wiring = _wiring(repo)

    shared = {
        "name": facts.get("name"),
        "readme_purpose": facts.get("purpose"),
        "category": facts.get("category"),
        "host": facts.get("host"),
        "wiring": wiring,
    }
    surface = {
        "controllers": [
            {
                "name": c.name,
                "file": c.file,
                "endpoints": [
                    {"http": e.http, "route": e.route, "handler": e.handler,
                     "secured": e.secured, "role": e.role, "summary": e.summary}
                    for e in c.endpoints
                ],
            }
            for c in controllers
        ],
        "services": [
            {"interface": s.interface, "impl": s.impl, "file": s.file,
             "collaborators": s.collaborators}
            for s in services
        ],
        "domain_types": [{"name": t.name, "file": t.file} for t in domain],
        "capability_stories": [c.story for c in caps],
    }
    return Evidence(
        target=repo.name,
        sections={"purpose": {"summary": shared}, "requirements": surface},
        shared=shared,
    )


def capability_summary(repo: Path) -> dict:
    """Per-repo capability roll-up for the L0 platform capability map."""
    caps = _extract_capabilities(repo)
    return {
        "category": _service_facts(repo).get("category"),
        "total": len(caps),
        "secured": sum(1 for c in caps if c.secured),
        "public_mutating": [f"{c.endpoint.http} {c.endpoint.route}" for c in caps if c.public_mutating],
        "stories": [c.story for c in caps],
    }


# --------------------------------------------------------------------------------- render

def _md_service(f: dict) -> str:
    lines = [f"# {f['name']} — service (L1)", ""]
    if f["purpose"]:
        lines += [f"{f['purpose']}", ""]
    lines += ["👉 **What it does for people:** see `capabilities.md` (the end-user view). This doc "
              "is the service's identity and deployment facts.", ""]
    lines += ["## Identity", ""]
    lines += [f"- **App name:** `{f['name']}`"]
    if f.get("kind"):
        lines.append(f"- **Kind:** `{f['kind']}`")
    if f["host"]:
        lines.append(f"- **Deployed host:** {f['host']}")
    if f["app_label"]:
        if f["category"]:
            lines.append(f"- **App / category:** `{f['app_label']}` / `{f['category']}`")
        else:
            lines.append(f"- **App:** `{f['app_label']}` _(bare host, no category)_")
    if f["ping"]:
        lines.append(f"- **Liveness:** `{f['ping']}` → `pong`")
    if f["gcp_project"]:
        lines.append(f"- **GCP project:** `{f['gcp_project']}`")
    if f["runtime"]:
        lines.append(f"- **Runtime:** `{f['runtime']}`" + (f" (JDK {f['jdk']})" if f["jdk"] else ""))
    lines.append("")
    # Technical footer: low-level release facts, demoted below identity. Version drift is a quiet
    # footnote here, not the headline — the headline signal lives in capabilities.md (Exposure).
    lines += ["## Technical facts", ""]
    versions = f["versions"]
    if versions:
        version_str = ", ".join(f"`{ver}` ({src})" for src, ver in versions.items())
        lines.append(f"- **Release version:** {version_str}")
        if f["drift"]:
            lines.append("  - _⚠ version drift: the sources above disagree; reconcile the lagging "
                         "source to the intended release._")
    lines.append("")
    return "\n".join(lines)


def _md_controllers(controllers: list[Controller]) -> str:
    lines = ["# Controllers (L2)", "", "HTTP surface derived from Micronaut annotations.", ""]
    lines += ["## Route table", "", "| Method | Route | Handler | Secured | Role | Permissions |",
              "|---|---|---|---|---|---|"]
    unsecured: list[tuple[str, str]] = []
    for ctrl in controllers:
        for ep in ctrl.endpoints:
            sec = "yes" if ep.secured else "**no**"
            role = ep.role or ""
            perms = ep.permissions or ""
            if ep.allow_internal:
                perms = (perms + " +internal").strip()
            lines.append(f"| {ep.http} | `{ep.route}` | {ctrl.name}.{ep.handler} | {sec} | {role} | {perms} |")
            if not ep.secured:
                unsecured.append((f"{ep.http} {ep.route}", ctrl.name))
    lines.append("")
    if unsecured:
        lines += ["## Unsecured endpoints", "",
                  "Deliberately public (no `@Secure`) — verify each is intended:", ""]
        for route, cname in unsecured:
            lines.append(f"- `{route}` — {cname}")
        lines.append("")
    lines += ["## Dependency injection", ""]
    for ctrl in controllers:
        deps = [t for t, _ in ctrl.injects] + ctrl.constructor_params
        deps = [d for d in deps if d]
        dep_str = ", ".join(f"`{d}`" for d in deps) if deps else "_none_"
        lines.append(f"- **{ctrl.name}** (`@Controller(\"{ctrl.prefix}\")`) → {dep_str}")
    lines.append("")
    return "\n".join(lines)


def _md_services(services: list[Service]) -> str:
    lines = ["# Services (L2)", "",
             "Service registry — the `interface + Default<Name>` pattern.", "",
             "| Interface | Implementation | Singleton | Collaborators |", "|---|---|---|---|"]
    for s in services:
        iface = s.interface or "—"
        impl = s.impl or "—"
        singleton = "yes" if s.singleton else ""
        collab = ", ".join(f"`{c}`" for c in s.collaborators) if s.collaborators else ""
        lines.append(f"| {iface} | {impl} | {singleton} | {collab} |")
    lines.append("")
    return "\n".join(lines)


def _md_domain(types: list[DomainType]) -> str:
    lines = ["# Domain model (L2)", "", "Domain types (POGOs / enums).", ""]
    for t in types:
        lines.append(f"## {t.name} ({t.kind})")
        if t.kind == "enum" and t.values:
            lines.append("")
            lines.append("Values: " + ", ".join(f"`{v}`" for v in t.values))
        elif t.fields:
            lines.append("")
            for fld in t.fields:
                lines.append(f"- `{fld}`")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- platform signals

def _platform_signals(repo: Path) -> dict:
    """This repo's value for each platform-scoped signal key (see PLATFORM_SIGNAL_SPECS)."""
    build = _read(repo, "build.gradle") or ""
    app_yaml = _read(repo, "src/main/appengine/app.yaml") or ""
    application_yml = _read(repo, "src/main/resources/application.yml") or ""
    gradle_props = _read(repo, "gradle.properties") or ""
    deploy_yml = _read(repo, ".github/workflows/deploy.yml") or ""

    jacoco_m = re.search(r"minimum\s*=\s*([\d.]+)", build)
    mn_m = re.search(r"micronautVersion\s*=\s*(\S+)", gradle_props)
    test_m = re.search(r'testRuntime\(\s*["\'](\w+)["\']', build)
    jdk_m = re.search(r"JDK_VERSION:\s*(\d+)", deploy_yml) or re.search(r"runtime:\s*java(\d+)", app_yaml)
    ping = any(ep.route.endswith("/ping") for c in _all_controllers(repo) for ep in c.endpoints)

    mn_bom = mn_m.group(1) if mn_m else None
    mn_plugin = _micronaut_plugin_version(build)
    # Aligned only when both are known; None (unknown) when either is absent so we don't
    # invent a mismatch for a repo that declares neither.
    aligned = (mn_bom == mn_plugin) if (mn_bom and mn_plugin) else None

    return {
        "security.enabled": _yaml_bool(_yaml_direct_child(application_yml, "security", "enabled")),
        "https.secure_always": bool(re.search(r"secure:\s*always", app_yaml)),
        "https.redirect": bool(re.search(r"http-to-https-redirect:\s*true", application_yml)),
        "coverage.gate": bool(re.search(r"build\.dependsOn\s+jacocoTestCoverageVerification", build)),
        "ping.present": ping,
        "micronaut.version_aligned": aligned,
        "jdk": jdk_m.group(1) if jdk_m else None,
        "micronaut.version": mn_bom,
        "micronaut.plugin.version": mn_plugin,
        "test.runtime": test_m.group(1) if test_m else None,
        "coverage.minimum": jacoco_m.group(1) if jacoco_m else None,
    }


def _fmt_signal(spec: SignalSpec, value) -> str:
    if value is None:
        return "—"
    if spec.type == "bool":
        return "yes" if value else "no"
    return f"`{value}`"


def _classify(repo: Path) -> str:
    """Derive a repo's kind from structural signals (config overrides take precedence upstream)."""
    name = repo.name
    if name.startswith("template-"):
        return "template"
    if name.endswith("-tester"):
        return "tester"
    build = _read(repo, "build.gradle") or ""
    if "java-gradle-plugin" in build or "gradlePlugin" in build or "io.micronaut.library" in build:
        return "library"
    has_appengine = (repo / "src/main/appengine/app.yaml").exists()
    if not has_appengine and ("maven-publish" in build or "publishing" in build or "java-library" in build):
        return "library"
    if has_appengine or "io.micronaut.application" in build:
        return "service"
    return "experiment"  # has source but none of a service/library/tester/template marker


def classify(repo: Path) -> str:
    """A repo's kind: an authored override if present, else derived from code."""
    return repo_kind_override(repo.name) or _classify(repo)


# ------------------------------------------------------------------------------- adapter

class MicronautGroovyAdapter:
    name = "micronaut_groovy"

    def detect(self, repo: Path) -> bool:
        build = _read(repo, "build.gradle") or ""
        return "micronaut" in build.lower() or (repo / "src" / "main" / "groovy").exists()

    def classify(self, repo: Path) -> str:
        return classify(repo)

    def extract_capabilities(self, repo: Path) -> Node:
        caps = _extract_capabilities(repo)
        f = _service_facts(repo)
        provenance = sorted({src for c in caps for src in c.source_files})
        return Node(
            level=Level.L1, kind="capabilities", id="capabilities", path="capabilities.md",
            body=_md_capabilities(caps, f), derived_from=provenance, supports_authored=True,
        )

    def capability_summary(self, repo: Path) -> dict:
        return capability_summary(repo)

    def extract_evidence(self, repo: Path) -> Evidence:
        return build_evidence(repo)

    def extract_overview(self, repo: Path) -> Node:
        facts = _service_facts(repo)
        wiring = _wiring(repo)
        risks = _risk_notes(repo)
        hashes = build_evidence(repo).hashes()
        provenance = sorted({c.file for c in _all_controllers(repo)}
                            | {s.file for s in _parse_services(repo)}
                            | {p for p in ["README.md", "build.gradle"] if (repo / p).exists()})
        return Node(
            level=Level.L1, kind="overview", id="overview", path="overview.md",
            body=_md_overview(repo, facts, wiring, risks, hashes),
            derived_from=provenance, supports_authored=True,
        )

    def extract_modules(self, repo: Path) -> list[Node]:
        controllers = _all_controllers(repo)
        services = _parse_services(repo)
        domain = _parse_domain(repo)
        return [
            Node(Level.L2, "module", "controllers", "modules/controllers.md",
                 _md_controllers(controllers),
                 derived_from=sorted({c.file for c in controllers})),
            Node(Level.L2, "module", "services", "modules/services.md",
                 _md_services(services),
                 derived_from=sorted({_rel(repo, p) for p in iter_files(repo, "src/main")
                                      if _is_service_file(p)})),
            Node(Level.L2, "module", "domain", "modules/domain.md",
                 _md_domain(domain),
                 derived_from=sorted({t.file for t in domain})),
        ]

    def platform_signal_specs(self) -> list:
        return PLATFORM_SIGNAL_SPECS

    def platform_signals(self, repo: Path) -> dict:
        return _platform_signals(repo)

