"""Authored platform policy: `system-model/platform.toml` (optional).

Small hand-authored config that overrides what can't be derived from code — chiefly repo
*intent* (e.g. an experiment that structurally looks like a service). Everything else is
derived; this file is only for exceptions and policy.
"""
from __future__ import annotations

import tomllib
from functools import lru_cache

from systemmodel.core.locate import platform_root

# Which kinds the platform model aggregates invariants/conventions over, by default.
DEFAULT_AGGREGATE_KINDS = ("service",)


def config_path():
    return platform_root() / "platform.toml"


@lru_cache(maxsize=1)
def _load() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def repo_kind_override(name: str) -> str | None:
    """A hand-declared kind for a repo, or None to fall back to derivation."""
    repos = _load().get("repos", {})
    return repos.get(name) if isinstance(repos, dict) else None


def aggregate_kinds() -> list[str]:
    """Repo kinds the platform model aggregates over (default: services only).

    Only a non-empty TOML array is honored; a scalar/typo falls back to the default so a
    malformed `aggregate_kinds = "service"` can't silently become a list of characters.
    """
    kinds = _load().get("policy", {}).get("aggregate_kinds")
    if isinstance(kinds, list) and all(isinstance(k, str) for k in kinds) and kinds:
        return list(kinds)
    return list(DEFAULT_AGGREGATE_KINDS)
