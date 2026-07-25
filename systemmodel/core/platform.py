"""L0 platform model: aggregate per-repo signals into platform-wide invariants.

System-agnostic. Adapters declare which of their signals are platform-scoped (via
SignalSpec) and report each repo's value; this module aggregates across repos —
computing what holds platform-wide and, crucially, which repos deviate (outliers).
A platform invariant is one the code of (nearly) every repo satisfies: code is truth,
lifted one altitude.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from systemmodel.core.schema import Level, Node


@dataclass(frozen=True)
class SignalSpec:
    """Describes a platform-scoped signal an adapter can report per repo."""

    key: str
    label: str
    kind: str  # "invariant" | "convention"
    type: str  # "bool" | "value"
    # Signals where matching the newest value in use is unambiguously better, so trailing the
    # norm is worth a nudge. Not true of every value signal: a repo whose coverage minimum sits
    # above the norm is ahead of it, and telling it to converge downwards would be bad advice.
    advisory: bool = False


# Sentinel distinguishing "no authored requirement" from an authored value that happens to
# be None. A signal with authored != _UNSET is prescriptive (required); otherwise descriptive.
_UNSET = object()


@dataclass
class Aggregate:
    spec: SignalSpec
    pairs: list[tuple[str, object]]  # (repo, value) for every reporting repo
    counts: dict[object, int] = field(default_factory=dict)
    expected: object = None          # descriptive norm (mode of real values)
    authored: object = _UNSET        # prescriptive required value, or _UNSET if not authored
    exceptions: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.pairs)

    @property
    def is_required(self) -> bool:
        return self.authored is not _UNSET

    def is_excepted(self, repo: str) -> bool:
        return repo in self.exceptions

    def violators(self) -> list[tuple[str, object]]:
        """(repo, value) pairs that violate the authored requirement; [] if not required."""
        if not self.is_required:
            return []
        return [(r, v) for r, v in self.pairs
                if v != self.authored and not self.is_excepted(r)]

    def excepted(self) -> list[tuple[str, object, str]]:
        if not self.is_required:
            return []
        return [(r, v, self.exceptions[r]) for r, v in self.pairs
                if v != self.authored and self.is_excepted(r)]


@dataclass(frozen=True)
class Conformance:
    """The `derived ≠ authored` gap across all platform signals (authored ones only)."""

    required: list[Aggregate]           # all authored (is_required) signals
    violating_signals: list[Aggregate]  # the subset with at least one violator
    repos_in_violation: list[str]       # sorted union of repos violating any requirement
    excepted_signals: list[Aggregate]


def conformance(aggs: dict[str, Aggregate]) -> Conformance:
    """Compute the authored-intent conformance summary. Only prescriptive signals count."""
    required = [a for a in aggs.values() if a.is_required]
    violating = [a for a in required if a.violators()]
    repos = sorted({r for a in required for r, _ in a.violators()})
    return Conformance(required=required, violating_signals=violating, repos_in_violation=repos,
                       excepted_signals=[a for a in required if a.excepted()])


def exception_lines(conf: Conformance) -> list[str]:
    return [f"{a.spec.label}: {repo}=`{_disp(value)}`" + (f" — {reason}" if reason else "")
            for a in conf.excepted_signals for repo, value, reason in a.excepted()]


def aggregate(records: list[tuple[str, dict]], specs: dict[str, SignalSpec],
              authored: dict[str, object] | None = None,
              exceptions: dict[str, dict[str, str]] | None = None) -> dict[str, Aggregate]:
    """Group per-repo signal values by key; expected = most common; attach authored intent."""
    authored = authored or {}
    exceptions = exceptions or {}
    by_key: dict[str, list[tuple[str, object]]] = {}
    for repo, signals in records:
        for key, value in signals.items():
            by_key.setdefault(key, []).append((repo, value))

    aggs: dict[str, Aggregate] = {}
    for key, pairs in by_key.items():
        spec = specs.get(key)
        if not spec:
            continue
        counts: dict[object, int] = {}
        for _, value in pairs:
            counts[value] = counts.get(value, 0) + 1
        # Expected value = the most common *real* value. None ("unset") never becomes the
        # norm, otherwise a signal most repos don't emit would flag the ones that do.
        real = {v: c for v, c in counts.items() if v is not None}
        expected = (max(real.items(), key=lambda kv: (kv[1], str(kv[0])))[0]
                    if real else None)
        aggs[key] = Aggregate(spec=spec, pairs=sorted(pairs), counts=counts, expected=expected,
                              authored=authored.get(key, _UNSET),
                              exceptions=dict(exceptions.get(key, {})))
    return aggs


def trailing_conventions(repo_name: str, aggs: dict[str, Aggregate]) -> list[tuple[str, object, object]]:
    """(label, this repo's value, the observed norm) for value signals where the repo trails.

    Deliberately scoped to *unrequired* value signals — dependency versions and the like. Those
    are not platform violations (see the note in the rendered invariants), so they are never
    reported as drift; they surface once, in the change brief for a repo someone is already
    editing, which is when upgrading them is nearly free.
    """
    trailing: list[tuple[str, object, object]] = []
    for agg in aggs.values():
        if not agg.spec.advisory or agg.is_required or agg.expected is None:
            continue
        value = next((v for r, v in agg.pairs if r == repo_name), None)
        if value is not None and value != agg.expected:
            trailing.append((agg.spec.label, value, agg.expected))
    return sorted(trailing, key=lambda t: t[0])


def display_value(value: object) -> str:
    """Human display for a signal value: None -> —, bools -> yes/no, else str."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


_disp = display_value  # internal shorthand used throughout this module


def _excepted_suffix(agg: Aggregate) -> str:
    excepted = agg.excepted()
    if not excepted:
        return ""
    detail = "; ".join(f"{r}=`{_disp(v)}`" + (f" — {reason}" if reason else "")
                       for r, v, reason in excepted)
    return f"  · excepted by `platform.toml` ({len(excepted)}): {detail}"


def _invariant_line(agg: Aggregate) -> str:
    """Bool invariant. Prescriptive (authored) -> conform/violations; else descriptive."""
    if agg.is_required:
        violators = [r for r, _ in agg.violators()]
        conform = agg.total - len(violators) - len(agg.excepted())
        line = f"- **{agg.spec.label}:** REQUIRED `{_disp(agg.authored)}` — {conform}/{agg.total} conform"
        if violators:
            line += "  ⚠ violations: " + ", ".join(violators)
        return line + _excepted_suffix(agg)
    # No authored requirement means no one asked for this to hold, so naming "outliers" would be
    # nagging about a spread nobody chose. Report the spread and stop.
    held = agg.total - len([r for r, v in agg.pairs if not v])
    return f"- **{agg.spec.label}:** {held}/{agg.total} repos (observed; not a requirement)"


def _convention_line(agg: Aggregate) -> str:
    """Value convention. Prescriptive (authored) -> conform/violations; else descriptive."""
    breakdown = ", ".join(
        f"`{_disp(v)}`×{c}"
        for v, c in sorted(agg.counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    )
    if agg.is_required:
        deviating = [(r, v) for r, v in agg.pairs
                     if v is not None and v != agg.authored and not agg.is_excepted(r)]
        unset = [r for r, v in agg.pairs if v is None and not agg.is_excepted(r)]
        conform = agg.total - len(deviating) - len(unset) - len(agg.excepted())
        line = (f"- **{agg.spec.label}:** REQUIRED `{_disp(agg.authored)}` — "
                f"{conform}/{agg.total} conform ({breakdown})")
        if deviating:
            line += "  ⚠ " + ", ".join(f"{r}=`{_disp(v)}`" for r, v in deviating)
        if unset:
            line += "  · unset: " + ", ".join(unset)
        return line + _excepted_suffix(agg)
    return f"- **{agg.spec.label}:** {breakdown} (observed; not a requirement)"


def _graph_section(graph, aggregated_repos: list[str]) -> list[str]:
    """The lead read: what depends on what. Nothing else here is as expensive to work out."""
    if graph is None:
        return []
    hubs = [(r, n) for r, n in graph.hubs() if r in set(aggregated_repos)]
    lines = ["## Service graph", "",
             "Who depends on whom, derived by matching each service's outbound hosts — named in "
             "its own source or hardcoded inside a shared client library it uses — against the "
             "hosts every other service answers on. This is the blast radius of a change.", "",
             f"- **Edges:** {graph.edge_count()} across {len(graph.calls)} repos", ""]
    if hubs:
        lines += ["**Most depended on** — changing these is the expensive kind of change:", ""]
        for repo, count in hubs[:8]:
            callers = ", ".join(graph.callers_of(repo))
            lines.append(f"- **{repo}** ← {count} consumers: {callers}")
        lines.append("")
    isolated = [r for r in graph.isolated() if r in set(aggregated_repos)]
    if isolated:
        lines += [f"**No edges either way** ({len(isolated)}): {', '.join(isolated)}", "",
                  "Either genuinely standalone, or reached by a path this scan cannot see "
                  "(a browser, a scheduled job, or CI rather than service code).", ""]
    return lines


def render_platform(
    aggs: dict[str, Aggregate],
    census: dict[str, list[str]],
    aggregated_kinds: list[str],
    aggregated_repos: list[str],
    adapters_used: set[str],
    graph=None,
    exposure: list[tuple[str, dict]] | None = None,
) -> list[Node]:
    """Render the aggregates + repo census into L0 platform Nodes.

    `census` maps repo kind -> repo names (every detected repo). `aggregated_repos` are the
    repos actually fed into the aggregation (services whose signals were read) — the honest
    provenance for the invariant/convention nodes. Libraries/testers/experiments don't
    pollute the outlier lists.
    """
    ordered = list(aggs.values())
    invariants = [a for a in ordered if a.spec.kind == "invariant"]
    conventions = [a for a in ordered if a.spec.kind == "convention"]
    aggregated_repos = sorted(aggregated_repos)
    all_repos = sorted(r for repos in census.values() for r in repos)

    index = [
        "# Platform model (L0)",
        "",
        "How the platform hangs together, derived from the code of every repo. This is the "
        "platform peer of a repo's model — the `~/.claude` to a repo's project config.",
        "",
        f"- **Repos scanned:** {len(all_repos)}",
        f"- **Aggregated over:** {len(aggregated_repos)} repos of kind {', '.join(aggregated_kinds)}",
        "",
    ]
    index += _graph_section(graph, aggregated_repos)
    index += [
        "## Repo census",
        "",
        "Each repo's kind (service invariants apply only to services). Kinds are derived from "
        "code; `platform.toml` can override.",
        "",
    ]
    for kind in sorted(census):
        repos = sorted(census[kind])
        index.append(f"- **{kind}** ({len(repos)}): {', '.join(repos)}")

    # Conformance = the derived ≠ authored gap. Only prescriptive (authored) signals count.
    conf = conformance(aggs)
    index += ["", "## Conformance (authored intent)", ""]
    if not conf.required:
        index.append("No authored requirements yet — the model is descriptive only. "
                     "Add `[invariants]`/`[conventions]` to `platform.toml` to require values.")
    elif not conf.violating_signals:
        excused = len(exception_lines(conf))
        index.append(f"✅ All {len(conf.required)} authored requirements hold across "
                     f"{len(aggregated_repos)} repos"
                     + (f", with {excused} authored exception(s) below." if excused else "."))
    else:
        index.append(f"- **Authored requirements:** {len(conf.required)}")
        index.append(f"- **Signals with violations:** {len(conf.violating_signals)}")
        index.append(f"- **Repos in violation:** {', '.join(conf.repos_in_violation)}")
        index.append("")
        index.append("Each violation is a `derived ≠ authored` gap — fix the code, or change the "
                     "spec in `platform.toml`. See invariants.md / conventions.md for specifics.")
    excused_lines = exception_lines(conf)
    if excused_lines:
        index += ["", "### Authored exceptions", "",
                  "These repos do **not** satisfy a requirement and are knowingly excused by "
                  "`[[exceptions]]` in `platform.toml`, so `--gate` passes. The requirement still "
                  "applies to every other service; each exemption is one repo, one signal, and "
                  "stands or falls on the reason given:", ""]
        index += [f"- {line}" for line in excused_lines]
    exposed = [(r, s) for r, s in sorted(exposure or []) if s.get("public_mutating")]
    index += ["", "## Unauthenticated writes", ""]
    if exposed:
        index += ["Mutating endpoints with no `@Secure`, excluding routes that are public by "
                  "design (login, logout, OAuth callbacks, webhooks). Verify each is intended:", ""]
        for repo, summary in exposed:
            routes = ", ".join(f"`{r}`" for r in summary["public_mutating"])
            index.append(f"- **{repo}**: {routes}")
    else:
        index.append("None — every mutating endpoint is either secured or public by design.")

    index += ["", "See `invariants.md` for the per-signal detail and `graph.md` for every edge.", ""]
    provenance = aggregated_repos

    inv_body = ["# Platform invariants (L0)", "",
                "**REQUIRED** lines are authored intent — violations are drift. Other lines are the "
                "observed norm across services, not a requirement. A repo marked `excepted by "
                "platform.toml` fails that one requirement on purpose: it is excused from that "
                "signal only, with the reason inline, and is not counted as conforming.", "",
                "Dependency versions are deliberately absent: in a scale-to-zero estate they drift "
                "as releases land, and the convention is to upgrade a repo when you are already in "
                "it — so a version lag is surfaced in that repo's change brief, not held open here "
                "as a standing platform violation.", ""]
    for a in invariants:
        inv_body.append(_invariant_line(a))
    inv_body.append("")
    for a in conventions:
        inv_body.append(_convention_line(a))
    inv_body.append("")

    nodes = [
        Node(Level.L0, "platform", "platform", "platform.md",
             "\n".join(index), derived_from=all_repos),
        Node(Level.L0, "invariant", "platform-invariants", "invariants.md",
             "\n".join(inv_body), derived_from=provenance),
    ]
    if graph is not None:
        nodes.append(render_graph(graph, aggregated_repos))
    return nodes


def _edge_label(graph, caller: str, target: str) -> str:
    mediators = graph.mediators_of(caller, target)
    if not mediators:
        return target
    return f"{target} (via `{'`, `'.join(mediators)}`)"


def render_graph(graph, aggregated_repos: list[str]) -> Node:
    """Every edge, both directions, so an agent can compute a blast radius without re-deriving."""
    scope = sorted(set(aggregated_repos))
    body = ["# Service graph (L0)", "",
            "Every service-to-service edge on the platform. `calls` is derived from outbound "
            "hostnames in a repo's own source plus the hosts its shared client libraries reach "
            "on its behalf; `consumed by` is that relation inverted.", "",
            "An edge tagged `via X` is library-mediated: the repo never names the host, it "
            "declares a collaborator of type `X` whose library hardcodes the URL. Untagged edges "
            "are literal URLs in the repo's own source.", "",
            "Edges published from CI rather than service code (test-result events, deploy events) "
            "are not shown — this reports what the code does, not what the pipeline does.", ""]
    for repo in scope:
        calls = graph.callees_of(repo)
        callers = graph.callers_of(repo)
        if not calls and not callers:
            continue
        labelled = ", ".join(_edge_label(graph, repo, target) for target in calls)
        body.append(f"### {repo}")
        body.append(f"- calls → {labelled or '_(nothing)_'}")
        body.append(f"- consumed by → {', '.join(callers) if callers else '_(nothing)_'}")
        body.append("")
    return Node(Level.L0, "graph", "platform-graph", "graph.md",
                "\n".join(body), derived_from=scope)

