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


@dataclass
class Aggregate:
    spec: SignalSpec
    pairs: list[tuple[str, object]]  # (repo, value) for every reporting repo
    counts: dict[object, int] = field(default_factory=dict)
    expected: object = None

    @property
    def total(self) -> int:
        return len(self.pairs)


def aggregate(records: list[tuple[str, dict]], specs: dict[str, SignalSpec]) -> dict[str, Aggregate]:
    """Group per-repo signal values by key; expected value = the most common one."""
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
        aggs[key] = Aggregate(spec=spec, pairs=sorted(pairs), counts=counts, expected=expected)
    return aggs


def _disp(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _invariant_line(agg: Aggregate) -> str:
    """Bool invariant: how many repos hold it (value True), and who doesn't."""
    non_holders = [r for r, v in agg.pairs if not v]
    held = agg.total - len(non_holders)
    line = f"- **{agg.spec.label}:** {held}/{agg.total} repos"
    if non_holders:
        line += "  ⚠ outliers: " + ", ".join(non_holders)
    return line


def _convention_line(agg: Aggregate) -> str:
    """Value convention: the expected value + a breakdown; deviating and unset repos named."""
    breakdown = ", ".join(
        f"`{_disp(v)}`×{c}"
        for v, c in sorted(agg.counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    )
    line = f"- **{agg.spec.label}:** expected `{_disp(agg.expected)}` ({breakdown})"
    # A repo that sets a different real value has drifted; one that sets nothing is "unset".
    deviating = [(r, v) for r, v in agg.pairs if v is not None and v != agg.expected]
    unset = [r for r, v in agg.pairs if v is None]
    if deviating:
        line += "  ⚠ " + ", ".join(f"{r}=`{_disp(v)}`" for r, v in deviating)
    if unset:
        line += "  · unset: " + ", ".join(unset)
    return line


def render_platform(
    aggs: dict[str, Aggregate],
    census: dict[str, list[str]],
    aggregated_kinds: list[str],
    aggregated_repos: list[str],
    adapters_used: set[str],
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
        "System-wide invariants and conventions, derived by aggregating the code of every "
        "repo in the platform. This is the platform peer of a repo's `.systemmodel/` — the "
        "`~/.claude` to a repo's project config.",
        "",
        f"- **Repos scanned:** {len(all_repos)}",
        f"- **Aggregated over:** {len(aggregated_repos)} repos of kind {', '.join(aggregated_kinds)}",
        f"- **Adapters:** {', '.join(sorted(adapters_used)) or 'none'}",
        "",
        "## Repo census",
        "",
        "Each repo's kind (service invariants apply only to services). Kinds are derived from "
        "code; `platform.toml` can override.",
        "",
    ]
    for kind in sorted(census):
        repos = sorted(census[kind])
        index.append(f"- **{kind}** ({len(repos)}): {', '.join(repos)}")
    index += [
        "",
        "An invariant's `N/total` is how many aggregated repos satisfy it; ⚠ names the ones "
        "that don't (drift from the platform norm).",
        "",
    ]
    provenance = aggregated_repos

    inv_body = ["# Platform invariants (L0)", "",
                "Constraints (nearly) every service's code satisfies. Outliers are drift.", ""]
    for a in invariants:
        inv_body.append(_invariant_line(a))
    inv_body.append("")

    conv_body = ["# Platform conventions (L0)", "",
                 "Shared build/test/runtime choices; the expected value is the platform norm.", ""]
    for a in conventions:
        conv_body.append(_convention_line(a))
    conv_body.append("")

    return [
        Node(Level.L0, "platform", "platform", "platform.md",
             "\n".join(index), derived_from=all_repos),
        Node(Level.L0, "invariant", "platform-invariants", "invariants.md",
             "\n".join(inv_body), derived_from=provenance),
        Node(Level.L0, "convention", "platform-conventions", "conventions.md",
             "\n".join(conv_body), derived_from=provenance),
    ]
