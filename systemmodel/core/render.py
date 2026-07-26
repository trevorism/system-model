"""Render Nodes to a model doc tree + MANIFEST.json.

System-agnostic: it writes whatever Nodes an adapter produced, wrapping each in the frontmatter
envelope and recording provenance, content hash and section state in the manifest. The caller
decides the output root (a repo's model dir, or the platform root); this module just writes there
and prunes only the files it previously wrote.

Generated documents are wholly generated. Human writing lives in `intent.md`, which this module
never writes and never prunes — so there is no preserved-region machinery here, and nothing in a
generated file that a reader has to treat as load-bearing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from systemmodel.core.overlay import replace_section
from systemmodel.core.schema import GENERATOR_VERSION, Node, frontmatter

INTENT_FILE = "intent.md"


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


def recorded_state(root: Path) -> dict[str, dict]:
    """Per-document machine state from the manifest: evidence and anchor hashes.

    This is what used to sit in HTML comments inside the prose. It has no meaning to a reader,
    so it lives here instead — but the tool still needs it to decide what to regenerate and what
    has gone stale.
    """
    manifest = read_manifest(root) or {}
    return {n["path"]: {"regions": n.get("regions", {}),
                        "requirements": n.get("requirements", {})}
            for n in manifest.get("nodes", []) if "path" in n}


def _document(node: Node, *, adapter: str, prose: dict[str, str] | None) -> str:
    body = node.body
    for title, text in (prose or {}).items():
        body = replace_section(body, title, text)
    return f"{frontmatter(node, adapter=adapter)}\n\n{body.rstrip()}\n"


_LEGACY_STAMP = re.compile(r"<!-- decomposition evidence=([0-9a-f]*) -->")


def recorded_decomposition(root: Path) -> str:
    """The evidence the on-disk feature decomposition was built from.

    A repo-level fact, so it lives once at the manifest root rather than being repeated in every
    feature document — which is also what stops a re-derive from re-cutting the features for free.

    Falls back to the stamp's retired inline home. Without that, migrating a model written before
    the manifest carried it would re-cut the features of every repo, at one agent call each, for
    no reason other than the bookkeeping having moved.
    """
    stamp = (read_manifest(root) or {}).get("decomposition", "")
    if stamp:
        return stamp
    for path in sorted((root / "features").glob("*.md")):
        found = _LEGACY_STAMP.search(path.read_text(encoding="utf-8"))
        if found:
            return found.group(1)
    return ""


def build_manifest(nodes: list[Node], *, adapter: str, target: str, generated_at: str,
                   requirement_hashes: dict[str, dict[str, str]] | None = None,
                   decomposition: str = "") -> dict:
    hashes = requirement_hashes or {}
    return {
        "schema": "systemmodel/manifest@2",
        "decomposition": decomposition,
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
                # Lower-cased: the section title is the key everywhere it is looked up,
                # and a case mismatch here silently re-synthesizes on every run.
                "regions": {k.lower(): v for k, v in n.synth_sections.items()},
                "requirements": hashes.get(n.path, {}),
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
    synth_prose: dict[str, dict[str, str]] | None = None,
    requirement_hashes: dict[str, dict[str, str]] | None = None,
    decomposition: str = "",
) -> RenderResult:
    """Write the model tree into `out_root` (or preview if dry_run).

    Pruning is manifest-driven: only files this model wrote on a previous run are removed, so
    `intent.md`, `platform.toml` and sibling repo subdirs sharing the root are never touched.
    """
    root = out_root
    manifest = build_manifest(nodes, adapter=adapter, target=target,
                              generated_at=generated_at, requirement_hashes=requirement_hashes,
                              decomposition=decomposition)

    documents = {n.path: _document(n, adapter=adapter, prose=(synth_prose or {}).get(n.path))
                 for n in nodes}
    documents["MANIFEST.json"] = json.dumps(manifest, indent=2) + "\n"

    old = read_manifest(root)
    previous = [n["path"] for n in old.get("nodes", [])] + ["MANIFEST.json"] if old else []
    pruned = sorted(p for p in previous if p not in documents and p != INTENT_FILE)
    if not dry_run:
        for rel in pruned:
            dest = root / rel
            if dest.is_file():
                dest.unlink()
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


INTENT_TEMPLATE = """# Intent — {repo}

## Desired updates

"""


def ensure_intent(root, repo_name: str) -> bool:
    """Create the human-owned file, or refresh it while it is still untouched.

    Never overwrites anything a person wrote. "Untouched" means nothing under `## Desired updates`
    and no `## Applied` history — so boilerplate can be improved later without a hand migration,
    while a file with a single entry in it is off limits forever.
    """
    from systemmodel.core.overlay import section_body

    target = root / INTENT_FILE
    fresh = INTENT_TEMPLATE.format(repo=repo_name)
    if target.exists():
        current = target.read_text(encoding="utf-8")
        untouched = (not (section_body(current, "Desired updates") or "").strip()
                     and section_body(current, "Applied") is None)
        if not untouched or current == fresh:
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fresh, encoding="utf-8", newline="\n")
    return True
