"""CLI: derive a system model for a target repo and write it to <repo>/.systemmodel/.

    python -m systemmodel.derive <repo-name> [--dry-run] [--adapter NAME]

System-agnostic: it selects an adapter, runs the extractors, and renders. Any stack
knowledge lives in the chosen adapter.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from systemmodel.core import adapter as adapters
from systemmodel.core.adapter import extract_all
from systemmodel.core.locate import resolve_repo
from systemmodel.core.render import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="systemmodel.derive", description=__doc__)
    parser.add_argument("repo", help="target repo folder name (under DEV_DIR) or absolute path")
    parser.add_argument("--adapter", help="force a specific adapter instead of auto-detect")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    args = parser.parse_args(argv)

    try:
        repo = resolve_repo(args.repo)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not repo.exists():
        print(f"error: repo path does not exist: {repo}", file=sys.stderr)
        return 2

    try:
        adapter = adapters.select(repo, args.adapter)
    except (LookupError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    generated_at = datetime.now().isoformat(timespec="seconds")
    nodes = extract_all(adapter, repo)
    result = render(repo, nodes, adapter=adapter.name, generated_at=generated_at, dry_run=args.dry_run)

    verb = "would write" if args.dry_run else "wrote"
    print(f"adapter: {adapter.name}")
    print(f"target : {repo}")
    print(f"{verb} {len(result.files)} files under {result.root}:")
    for node in nodes:
        print(f"  [{node.level.value}] {node.path}  ({node.content_hash()})  <- {', '.join(node.derived_from) or '(none)'}")
    print("  MANIFEST.json")
    if result.pruned:
        pverb = "would prune" if args.dry_run else "pruned"
        print(f"{pverb} {len(result.pruned)} stale file(s): {', '.join(result.pruned)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
