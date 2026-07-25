"""Cross-repo service graph: who calls whom, derived by inverting outbound edges.

A single repo can see what it calls; it cannot see what calls *it*. That inverse is the
expensive question in a many-service platform ("if I change this, what breaks?"), and it is
purely deterministic — every repo already reports its own host and its outbound hosts, so the
consumer index falls out of matching one against the other.

Built once per process. The scan is cheap (host + outbound URLs per repo), so no cache file is
kept on disk and there is no staleness to reason about.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from systemmodel.core import adapter as adapters
from systemmodel.core.locate import dev_dir


@dataclass(frozen=True)
class ServiceGraph:
    host_to_repo: dict[str, str]
    calls: dict[str, list[str]]
    consumed_by: dict[str, list[str]]

    def callers_of(self, repo_name: str) -> list[str]:
        return self.consumed_by.get(repo_name, [])

    def callees_of(self, repo_name: str) -> list[str]:
        return self.calls.get(repo_name, [])

    def unresolved_hosts(self) -> set[str]:
        return set()


def normalize_host(host: str) -> str:
    return host.replace("https://", "").replace("http://", "").rstrip("/").lower()


def build(repos: list[Path]) -> ServiceGraph:
    claims: dict[str, set[str]] = {}
    outbound: dict[str, list[str]] = {}

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

    # An alias claimed by more than one repo cannot identify a target, so drop it rather
    # than attribute an edge to an arbitrary winner.
    host_to_repo = {host: next(iter(owners)) for host, owners in claims.items() if len(owners) == 1}

    calls: dict[str, list[str]] = {}
    consumed_by: dict[str, list[str]] = {}
    for name, hosts in outbound.items():
        targets = sorted({host_to_repo[h] for h in hosts
                          if h in host_to_repo and host_to_repo[h] != name})
        calls[name] = targets
        for target in targets:
            consumed_by.setdefault(target, []).append(name)

    return ServiceGraph(
        host_to_repo=host_to_repo,
        calls=calls,
        consumed_by={k: sorted(v) for k, v in consumed_by.items()},
    )


@lru_cache(maxsize=1)
def service_graph() -> ServiceGraph:
    base = dev_dir()
    if not base.exists():
        return ServiceGraph({}, {}, {})
    repos = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    return build(repos)
