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

# Which kinds get a feature decomposition. Templates and experiments are excluded by default:
# a template has no intent of its own, only inherited scaffolding, and decomposing one produced
# requirements anchored on CI yaml that the index cannot resolve (46% anchored, against 95-100%
# for real repos). Spending an agent call to freeze weak permanent slugs is worse than nothing.
DEFAULT_FEATURE_KINDS = ("service", "library", "tester")


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


def feature_kinds() -> list[str]:
    """Repo kinds that get a feature decomposition (default: service, library, tester)."""
    kinds = _load().get("policy", {}).get("feature_kinds")
    if isinstance(kinds, list) and all(isinstance(k, str) for k in kinds) and kinds:
        return list(kinds)
    return list(DEFAULT_FEATURE_KINDS)


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


def authored_exceptions() -> dict[str, dict[str, str]]:
    entries = _load().get("exceptions", [])
    if not isinstance(entries, list):
        return {}
    by_signal: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        signal, repo, reason = entry.get("signal"), entry.get("repo"), entry.get("reason", "")
        if not (isinstance(signal, str) and signal and isinstance(repo, str) and repo):
            continue
        by_signal.setdefault(signal, {})[repo] = reason if isinstance(reason, str) else ""
    return by_signal


def acknowledged_exposure() -> dict[str, dict[str, str]]:
    """{repo: {route: reason}} for unauthenticated writes a human has reviewed and accepted.

    A "verify each is intended" list with no way to record *verified* decays into a block of
    text everyone skips — which is precisely when a genuinely new exposure slips through. An
    acknowledged route drops out of the review position and is listed separately for audit, so
    the section reports only what nobody has looked at yet.
    """
    entries = _load().get("acknowledged_exposure", [])
    if not isinstance(entries, list):
        return {}
    by_repo: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        repo, route, reason = entry.get("repo"), entry.get("route"), entry.get("reason", "")
        if not (isinstance(repo, str) and repo and isinstance(route, str) and route):
            continue
        by_repo.setdefault(repo, {})[route] = reason if isinstance(reason, str) else ""
    return by_repo


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
