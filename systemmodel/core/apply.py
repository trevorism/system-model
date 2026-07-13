"""`apply`: turn an edited model (spec) into a change brief for an agent.

The reverse of derivation. `derive` treats code as truth and (re)writes the model; `apply`
treats the on-disk model as *intent* — the developer edited the model's `*.md` to describe
desired state — and emits the `derived ≠ authored` gap as human/agent-actionable instructions.

system-model does not edit code itself. It computes the gap and points at the exact source
files (via each node's `derived_from` provenance); an agent (or the developer) makes the edits,
and `--check` is the acceptance test that re-derivation reproduces the edited spec.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from systemmodel.core.locate import model_root
from systemmodel.core.schema import Node


def _strip_frontmatter(text: str) -> str:
    """Return the body of a model doc, dropping a leading `---`…`---` frontmatter block."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1:] if nl != -1 else ""
    return text


def spec_gaps(repo: Path, nodes: list[Node]) -> list[tuple[Node, list[str]]]:
    """(node, unified-diff) for each on-disk model doc that differs from derived code.

    The `derived ≠ authored` gap per document: the on-disk model is the desired spec, `nodes`
    is the freshly-derived current state. Empty when there's no model on disk (nothing authored)
    or the code already matches the spec.
    """
    root = model_root(repo)
    changed: list[tuple[Node, list[str]]] = []
    for node in nodes:
        on_disk = root / node.path
        if not on_disk.exists():
            continue  # spec doesn't (yet) express this node — nothing to reconcile
        desired = _strip_frontmatter(on_disk.read_text(encoding="utf-8")).strip("\n")
        current = node.body.rstrip("\n")
        if desired == current:
            continue
        diff = difflib.unified_diff(
            current.splitlines(), desired.splitlines(),
            fromfile=f"current/{node.path}", tofile=f"desired/{node.path}", lineterm="",
        )
        changed.append((node, list(diff)))
    return changed


def build_brief(repo: Path, nodes: list[Node]) -> str | None:
    """Diff the edited on-disk model (desired) against freshly-derived nodes (current).

    Returns a change-brief string, or None if the code already matches the spec.
    """
    changed = spec_gaps(repo, nodes)
    if not changed:
        return None

    lines = [
        f"# Change brief for `{repo.name}`",
        "",
        "The developer edited this repo's system-model to describe **desired** state. Change the",
        "repo's **code** so that re-deriving its model reproduces that edited spec. system-model",
        "did not edit code — you do. Each section below shows one model document the developer",
        "changed, the diff (current derived → desired), and the source files that produce it.",
        "",
        f"**Acceptance:** after your edits, `uv run systemmodel {repo.name} --check` must be clean",
        "(re-derivation matches the edited spec).",
        "",
    ]
    for node, diff in changed:
        sources = ", ".join(f"`{s}`" for s in node.derived_from) or "_(no provenance recorded)_"
        lines += [
            f"## `{node.path}`  (level {node.level.value})",
            "",
            f"Edit these source files: {sources}",
            "",
            "```diff",
            *diff,
            "```",
            "",
        ]
    return "\n".join(lines)
