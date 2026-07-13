"""Authored platform policy: `platform.toml` at the standalone model root (optional).

Small hand-authored config that overrides what can't be derived from code — chiefly repo
*intent* (e.g. an experiment that structurally looks like a service). Everything else is
derived; this file is only for exceptions and policy. It lives at the root of the standalone
model dir, alongside the derived platform model.
"""
from __future__ import annotations

import tomllib
from functools import lru_cache

from systemmodel.core.locate import systemmodel_dir

# Which kinds the platform model aggregates invariants/conventions over, by default.
DEFAULT_AGGREGATE_KINDS = ("service",)


def config_path():
    return systemmodel_dir() / "platform.toml"


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


def _flatten(table: dict, prefix: str = "") -> dict[str, object]:
    """Flatten nested tables into dotted keys.

    TOML parses a dotted key (`security.enabled = true`) into nested tables
    (`{security: {enabled: true}}`); signal keys are dotted strings, so re-join them.
    """
    out: dict[str, object] = {}
    for k, v in table.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def authored_signals() -> dict[str, object]:
    """Prescriptive required/expected value per platform signal key.

    Merges the `[invariants]` (bool) and `[conventions]` (value) tables of platform.toml
    into one {signal_key: required_value} map. A signal present here is *required* — the
    platform model measures conformance against it and reports violators (the derived ≠
    authored gap). Absent keys stay descriptive. Non-table sections are ignored so a typo
    can't crash aggregation.
    """
    cfg = _load()
    authored: dict[str, object] = {}
    for section in ("invariants", "conventions"):
        table = cfg.get(section, {})
        if isinstance(table, dict):
            authored.update(_flatten(table))
    return authored
