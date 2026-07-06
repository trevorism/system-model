"""Render Nodes to the .systemmodel/ doc tree + MANIFEST.json.

System-agnostic: it writes whatever Nodes an adapter produced, wrapping each in the
frontmatter envelope and recording provenance + content hash in the manifest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from systemmodel.core.schema import GENERATOR_VERSION, Node, frontmatter

MODEL_DIRNAME = ".systemmodel"


@dataclass
class RenderResult:
    root: Path
    files: list[str]
    manifest: dict
    dry_run: bool
    pruned: list[str]


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
    target_repo: Path,
    nodes: list[Node],
    *,
    adapter: str,
    generated_at: str,
    dry_run: bool = False,
) -> RenderResult:
    """Write the model tree into <target_repo>/.systemmodel/ (or preview if dry_run)."""
    root = target_repo / MODEL_DIRNAME
    manifest = build_manifest(
        nodes, adapter=adapter, target=target_repo.name, generated_at=generated_at
    )

    documents: dict[str, str] = {}
    for node in nodes:
        documents[node.path] = _document(node, adapter=adapter)
    documents["MANIFEST.json"] = json.dumps(manifest, indent=2) + "\n"

    # Prune files from a previous run that this run no longer produces, so the tree on
    # disk always matches the manifest (no silently stale, misleading nodes).
    pruned: list[str] = []
    if root.exists():
        for existing in root.rglob("*"):
            if existing.is_file():
                rel = existing.relative_to(root).as_posix()
                if rel not in documents:
                    pruned.append(rel)
                    if not dry_run:
                        existing.unlink()

    written: list[str] = []
    for rel, content in documents.items():
        dest = root / rel
        written.append(rel)
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")

    # Remove now-empty directories left behind by pruning (deepest first).
    if not dry_run and root.exists():
        for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass  # not empty — keep it

    return RenderResult(root=root, files=written, manifest=manifest,
                        dry_run=dry_run, pruned=sorted(pruned))
