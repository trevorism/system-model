"""Render Nodes to a model doc tree + MANIFEST.json.

System-agnostic: it writes whatever Nodes an adapter produced, wrapping each in the
frontmatter envelope and recording provenance + content hash in the manifest. The caller
decides the output root (a repo's model dir, or the platform root); this module just writes
there and prunes only the files it previously wrote.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from systemmodel.core.schema import GENERATOR_VERSION, Node, frontmatter


@dataclass
class RenderResult:
    root: Path
    files: list[str]
    manifest: dict
    dry_run: bool
    pruned: list[str]


def read_manifest(root: Path) -> dict | None:
    """The MANIFEST.json at a model root as a dict, or None if absent/unreadable."""
    manifest = root / "MANIFEST.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _document(node: Node, *, adapter: str) -> str:
    fm = frontmatter(node, adapter=adapter)
    body = node.body.rstrip("\n")
    return f"{fm}\n\n{body}\n"


def build_manifest(nodes: list[Node], *, adapter: str, target: str, generated_at: str) -> dict:
    return {
        "schema": "systemmodel/manifest@1",
        "generator_version": GENERATOR_VERSION,
        "adapter": adapter,
        "target": target,
        "generated_at": generated_at,
        "nodes": [
            {
                "id": n.id,
                "level": n.level.value,
                "kind": n.kind,
                "path": n.path,
                "status": n.status,
                "content_hash": n.content_hash(),
                "derived_from": n.derived_from,
            }
            for n in nodes
        ],
    }


def render(
    out_root: Path,
    nodes: list[Node],
    *,
    adapter: str,
    target: str,
    generated_at: str,
    dry_run: bool = False,
) -> RenderResult:
    """Write the model tree into `out_root` (or preview if dry_run).

    `target` is recorded in the manifest. Pruning is manifest-driven: only files this model
    wrote on a previous run (per the on-disk MANIFEST.json) are removed. Unrelated files —
    e.g. platform.toml or sibling repo subdirs sharing the standalone root — are never touched.
    """
    root = out_root
    manifest = build_manifest(
        nodes, adapter=adapter, target=target, generated_at=generated_at
    )

    documents: dict[str, str] = {}
    for node in nodes:
        documents[node.path] = _document(node, adapter=adapter)
    documents["MANIFEST.json"] = json.dumps(manifest, indent=2) + "\n"

    # Prune only what a previous run recorded in the manifest and this run no longer
    # produces, so the tree matches the manifest without scanning (or deleting) anything
    # else that happens to live under a shared root.
    old = read_manifest(root)
    previous = [n["path"] for n in old.get("nodes", [])] + ["MANIFEST.json"] if old else []
    pruned = sorted(p for p in previous if p not in documents)
    if not dry_run:
        for rel in pruned:
            dest = root / rel
            if dest.is_file():
                dest.unlink()
            # Remove now-empty parent dirs left behind (deepest first, up to the root).
            parent = dest.parent
            while parent != root and parent.is_dir():
                try:
                    parent.rmdir()
                except OSError:
                    break  # not empty — keep it
                parent = parent.parent

    written: list[str] = []
    for rel, content in documents.items():
        dest = root / rel
        written.append(rel)
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")

    return RenderResult(root=root, files=written, manifest=manifest,
                        dry_run=dry_run, pruned=pruned)
