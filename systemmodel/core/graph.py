"""Cross-repo service graph: who calls whom, derived by inverting outbound edges.

A single repo can see what it calls; it cannot see what calls *it*. That inverse is the
expensive question in a many-service platform ("if I change this, what breaks?"), and it is
purely deterministic — every repo already reports its own host and its outbound hosts, so the
consumer index falls out of matching one against the other.

Outbound hosts arrive two ways: named literally in a repo's own source, or hardcoded inside a
shared client library the repo uses — the common case here, where a repo depends hard on a
service without ever spelling its name. Adapters report the second kind under `library_calls`;
both merge into one edge set, with the library ones attributed to the client type that carries
them so a reader can see why the edge is claimed.

Built once per process. The scan is cheap (host + outbound URLs per repo), so no cache file is
kept on disk and there is no staleness to reason about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from systemmodel.core import adapter as adapters
from systemmodel.core.locate import dev_dir


@dataclass(frozen=True)
class ServiceGraph:
    host_to_repo: dict[str, str]
    calls: dict[str, list[str]]
    consumed_by: dict[str, list[str]]
    mediated_by: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def callers_of(self, repo_name: str) -> list[str]:
        return self.consumed_by.get(repo_name, [])

    def callees_of(self, repo_name: str) -> list[str]:
        return self.calls.get(repo_name, [])

    def mediators_of(self, caller: str, target: str) -> list[str]:
        return self.mediated_by.get(caller, {}).get(target, [])

    def hubs(self, minimum: int = 2) -> list[tuple[str, int]]:
        """Services many others depend on, most-depended-on first — the risky things to change."""
        ranked = [(repo, len(callers)) for repo, callers in self.consumed_by.items()
                  if len(callers) >= minimum]
        return sorted(ranked, key=lambda rc: (-rc[1], rc[0]))

    def leaves(self) -> list[str]:
        """Services nothing else calls — safe to change, and candidates for retirement."""
        known = set(self.calls) | set(self.consumed_by)
        return sorted(r for r in known if not self.consumed_by.get(r))

    def isolated(self) -> list[str]:
        """Services with no edges in either direction."""
        known = set(self.calls) | set(self.consumed_by)
        return sorted(r for r in known
                      if not self.consumed_by.get(r) and not self.calls.get(r))

    def edge_count(self) -> int:
        return sum(len(targets) for targets in self.calls.values())


def normalize_host(host: str) -> str:
    return host.replace("https://", "").replace("http://", "").rstrip("/").lower()


def build(repos: list[Path]) -> ServiceGraph:
    claims: dict[str, set[str]] = {}
    outbound: dict[str, list[str]] = {}
    through_libraries: dict[str, dict[str, list[str]]] = {}

    for repo in repos:
        try:
            adapter = adapters.select(repo)
        except LookupError:
            continue
        describe = getattr(adapter, "wiring", None)
        if not callable(describe):
            continue
        try:
            info = describe(repo)
        except Exception:
            continue
        for alias in info.get("hosts") or []:
            claims.setdefault(normalize_host(alias), set()).add(repo.name)
        outbound[repo.name] = [normalize_host(h) for h in info.get("calls", [])]
        through_libraries[repo.name] = {normalize_host(host): list(clients)
                                        for host, clients in (info.get("library_calls") or {}).items()}

    # An alias claimed by more than one repo cannot identify a target, so drop it rather
    # than attribute an edge to an arbitrary winner.
    host_to_repo = {host: next(iter(owners)) for host, owners in claims.items() if len(owners) == 1}

    calls: dict[str, list[str]] = {}
    consumed_by: dict[str, list[str]] = {}
    mediated_by: dict[str, dict[str, list[str]]] = {}
    for name, hosts in outbound.items():
        direct = {host_to_repo[h] for h in hosts
                  if h in host_to_repo and host_to_repo[h] != name}
        via: dict[str, set[str]] = {}
        for host, clients in through_libraries.get(name, {}).items():
            target = host_to_repo.get(host)
            if target is None or target == name or target in direct:
                continue
            via.setdefault(target, set()).update(clients)
        targets = sorted(direct | set(via))
        calls[name] = targets
        if via:
            mediated_by[name] = {t: sorted(via[t]) for t in sorted(via)}
        for target in targets:
            consumed_by.setdefault(target, []).append(name)

    return ServiceGraph(
        host_to_repo=host_to_repo,
        calls=calls,
        consumed_by={k: sorted(v) for k, v in consumed_by.items()},
        mediated_by=mediated_by,
    )


@lru_cache(maxsize=1)
def service_graph() -> ServiceGraph:
    base = dev_dir()
    if not base.exists():
        return ServiceGraph({}, {}, {})
    repos = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    return build(repos)
