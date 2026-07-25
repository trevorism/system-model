"""Adapter interface + registry — the extensibility seam.

A new target system is a new Adapter, never a fork. The core selects the first
registered adapter whose `detect()` matches the target repo (or an explicit choice),
then calls its four extractors to produce the L1-L4 Nodes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from systemmodel.core.schema import Node


@runtime_checkable
class Adapter(Protocol):
    """Contract every target-system adapter implements.

    Each extractor returns pre-rendered Node(s); the core owns the envelope.
    """

    name: str

    def detect(self, repo: Path) -> bool:
        """True if this adapter understands the given repo."""
        ...

    def extract_service(self, repo: Path) -> Node:
        """L1: the service/repo as a whole."""
        ...

    def extract_capabilities(self, repo: Path) -> Node:
        """L1: the end-user view — what the service lets people/services do (user stories)."""
        ...

    def extract_modules(self, repo: Path) -> list[Node]:
        """L2: modules (controllers, services, domain, ...)."""
        ...

    def extract_conventions(self, repo: Path) -> Node:
        """L3: build/test/naming conventions."""
        ...

    def extract_invariants(self, repo: Path) -> Node:
        """L4: enforced invariants (coverage gate, security, transport, ...)."""
        ...

    def platform_signal_specs(self) -> list:
        """SignalSpecs for this adapter's platform-scoped signals (empty if unsupported)."""
        ...

    def platform_signals(self, repo: Path) -> dict:
        """This repo's value for each platform signal key (empty if unsupported)."""
        ...

    def classify(self, repo: Path) -> str:
        """Classify the repo (service | library | tester | template | experiment)."""
        ...


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    """Register an adapter instance by its `name`."""
    _REGISTRY[adapter.name] = adapter
    return adapter


def all_adapters() -> list[Adapter]:
    return list(_REGISTRY.values())


def get(name: str) -> Adapter:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown adapter '{name}'. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def select(repo: Path, name: str | None = None) -> Adapter:
    """Pick an adapter: the named one, or the first whose detect() matches."""
    # Importing the adapters package registers the built-ins.
    import systemmodel.adapters  # noqa: F401

    if name:
        return get(name)
    for adapter in all_adapters():
        if adapter.detect(repo):
            return adapter
    raise LookupError(
        f"No adapter matched {repo}. Available: {[a.name for a in all_adapters()]}"
    )


def extract_all(adapter: Adapter, repo: Path) -> list[Node]:
    """Run every extractor and return the flat list of Nodes.

    Order mirrors the intended read altitude: the service identity, then its end-user
    capabilities, then the L2 modules and the L3/L4 conventions/invariants beneath them.
    `extract_capabilities` is optional so an older adapter without it still works.
    """
    nodes: list[Node] = [adapter.extract_service(repo)]
    get_caps = getattr(adapter, "extract_capabilities", None)
    if callable(get_caps):
        nodes.append(get_caps(repo))
    nodes.extend(adapter.extract_modules(repo))
    nodes.append(adapter.extract_conventions(repo))
    nodes.append(adapter.extract_invariants(repo))
    return nodes
