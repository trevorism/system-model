"""`apply`: turn unmet authored requirements into a change brief for an agent.

The reverse of derivation. `derive` treats code as truth and (re)writes the model; `apply` treats
authored requirements as *intent* — obligations a human committed the system to — and emits the
ones the code does not meet.

This used to diff whole documents. That could not work: one fact was projected into several
sections a human had to keep consistent by hand, so doing exactly what a brief asked still left
`--check` dirty, and the next brief asked for the change to be undone. A requirement is one
obligation in one place, and acceptance is a verdict on the code rather than a text match, so
neither failure is reachable from here.

system-model still never edits code. It computes the gap, names the anchors, and an agent (or a
person) makes the edits; `--verify` is the acceptance test.
"""
from __future__ import annotations

from pathlib import Path

from systemmodel.core.features import load as load_features
from systemmodel.core.locate import model_root
from systemmodel.core.requirements import (
    UNVERIFIED, VIOLATED, Requirement, parse_blocks,
)


def authored_requirements(repo: Path) -> list[tuple[str, Requirement]]:
    """Every authored requirement in the model, as `(document path, requirement)`."""
    root = model_root(repo)
    if not root.exists():
        return []
    found: list[tuple[str, Requirement]] = []
    overview = root / "overview.md"
    if overview.is_file():
        found += [("overview.md", r) for r in parse_blocks(overview.read_text(encoding="utf-8"))
                  if r.is_authored]
    for slug, feature in sorted(load_features(root).items()):
        found += [(feature.path, r) for r in feature.requirements if r.is_authored]
    return found


def requirement_gaps(repo: Path) -> list[tuple[str, Requirement]]:
    """Authored requirements the code does not meet, or has not been checked against.

    An unverified obligation counts as a gap: intent nobody has confirmed is not evidence the
    system holds, and treating it as passing would let `--gate` go green on an unchecked claim.
    """
    return [(path, requirement) for path, requirement in authored_requirements(repo)
            if requirement.state in (VIOLATED, UNVERIFIED)]


def build_brief(repo: Path, gaps: list[tuple[str, Requirement]] | None = None,
                advisories: list[str] | None = None) -> str | None:
    """A change brief from unmet authored requirements, or None when there is nothing to close."""
    gaps = requirement_gaps(repo) if gaps is None else gaps
    if not gaps:
        return None

    lines = [
        f"# Change brief for `{repo.name}`",
        "",
        "Each item below is an authored requirement — an obligation someone committed this",
        "system to — that the code does not currently meet. Change the repo's **code** so that",
        "each one holds. Do not edit the system model: it is the spec, and it lives outside",
        "this repo.",
        "",
        f"**Acceptance:** `uv run systemmodel {repo.name} --verify` reports every requirement",
        "below as satisfied. Re-read the anchors and check the real code paths; a plausible",
        "-looking edit that does not actually close the obligation will fail that check.",
        "",
    ]
    for path, requirement in gaps:
        anchors = ", ".join(f"`{a}`" for a in requirement.anchors) or "_(none recorded)_"
        lines += [f"## {requirement.id} — {path}", "", requirement.body, "",
                  f"Anchored on: {anchors}", ""]
        if requirement.state == VIOLATED and requirement.finding:
            lines += [f"Why it currently fails: {requirement.finding}", ""]
        elif requirement.state == UNVERIFIED:
            lines += ["Not yet verified — confirm it holds, and make it hold if it does not.", ""]

    if advisories:
        lines += [
            "## While you're here",
            "",
            "This repo trails the platform norm on the following. None of it is a violation and "
            "none of it affects acceptance — but the cheapest moment to close a version gap is "
            "while the repo is already open.",
            "",
            *[f"- {note}" for note in advisories],
            "",
        ]
    return "\n".join(lines)
