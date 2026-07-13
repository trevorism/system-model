"""System-agnostic schema for the derived system model.

The model is a tree of Markdown docs (one Node per file) plus a machine-readable
MANIFEST.json. This module defines the Node/Manifest data shapes, the level/kind
vocabulary, frontmatter serialization, and stable content hashing.

Design note: a Node carries a pre-rendered Markdown `body` produced by an adapter
(the adapter knows how to *describe* a given stack), while this core owns the
*envelope* — frontmatter, file layout, hashing, and the manifest. That split keeps
the core free of any stack knowledge.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

GENERATOR_VERSION = "0.1.0"


class Level(str, Enum):
    """Altitude in the one hierarchy. L0 is platform-wide; L1 is a single service/repo."""

    L0 = "L0"  # platform (cross-repo)
    L1 = "L1"  # service / repo
    L2 = "L2"  # module
    L3 = "L3"  # convention
    L4 = "L4"  # invariant


@dataclass
class Node:
    """A single node of the model, rendered to one Markdown file under the model root.

    `path` is the file location relative to the model root (e.g. "modules/controllers.md").
    `derived_from` is the provenance: repo-relative source paths this node was derived from.
    `body` is adapter-produced Markdown (no frontmatter — the core adds that).
    """

    level: Level
    kind: str
    id: str
    path: str
    body: str
    derived_from: list[str] = field(default_factory=list)
    status: str = "derived"  # future: authored | mixed

    def content_hash(self) -> str:
        """Stable hash of the semantic content (body only, not the volatile frontmatter).

        Excluding generated_at/timestamps here is what makes re-runs diff-stable and gives
        later phases a cheap change-stream: the hash only moves when the code-derived facts do.
        """
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:16]


def _yaml_scalar(value: str) -> str:
    """Emit a YAML scalar, quoting only when needed to stay unambiguous."""
    if value == "":
        return '""'
    if any(c in value for c in ':#') or value.strip() != value:
        return '"' + value.replace('"', '\\"') + '"'
    return value


def frontmatter(node: Node, *, adapter: str) -> str:
    """Serialize a Node's frontmatter block (deterministic key order).

    Intentionally omits `generated_at`: a per-file timestamp would make every model
    file churn on every run and bury real drift in the diff. The run timestamp lives
    once in MANIFEST.json instead.
    """
    lines = ["---"]
    lines.append(f"level: {node.level.value}")
    lines.append(f"kind: {_yaml_scalar(node.kind)}")
    lines.append(f"id: {_yaml_scalar(node.id)}")
    lines.append(f"adapter: {_yaml_scalar(adapter)}")
    lines.append(f"status: {_yaml_scalar(node.status)}")
    if node.derived_from:
        lines.append("derived_from:")
        for src in node.derived_from:
            lines.append(f"  - {_yaml_scalar(src)}")
    else:
        lines.append("derived_from: []")
    lines.append(f"generator_version: {_yaml_scalar(GENERATOR_VERSION)}")
    lines.append("---")
    return "\n".join(lines)
