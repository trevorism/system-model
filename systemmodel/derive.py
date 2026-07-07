"""CLI: derive a system model for a target repo and write it to <repo>/.systemmodel/.

    uv run systemmodel <repo> [--dry-run] [--adapter NAME]
    uv run systemmodel <repo> --check      # drift check, no writes, exit 1 if stale
    uv run systemmodel --all               # every auto-detected repo in the container
    uv run systemmodel --all --check       # platform-wide staleness check (CI)
    uv run systemmodel --platform          # L0 platform model -> system-model/.systemmodel/
    uv run systemmodel <repo> --apply      # spec -> code: emit a change brief from the edited model

(equivalently: python -m systemmodel.derive ...)

System-agnostic: it selects an adapter, runs the extractors, and renders. Any stack
knowledge lives in the chosen adapter.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from systemmodel.core import adapter as adapters
from systemmodel.core.adapter import extract_all
from systemmodel.core.apply import build_brief
from systemmodel.core.config import aggregate_kinds, authored_signals
from systemmodel.core.locate import dev_dir, platform_root, resolve_repo
from systemmodel.core.platform import aggregate, render_platform
from systemmodel.core.render import MODEL_DIRNAME, render


def _load_manifest(repo: Path) -> dict | None:
    """A repo's checked-in MANIFEST.json as a dict, or None if absent/unreadable."""
    manifest = repo / MODEL_DIRNAME / "MANIFEST.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _drift(repo: Path, new_manifest: dict, pruned: list[str]) -> list[str]:
    """Human-readable drift lines comparing a freshly derived model to what's on disk."""
    old = _load_manifest(repo)
    if old is None:
        return ["no model on disk (never generated)"]
    lines: list[str] = []
    # A generator/schema bump means the on-disk tree is stale even if bodies are unchanged.
    if old.get("generator_version") != new_manifest.get("generator_version"):
        lines.append(f"generator_version: {old.get('generator_version')} -> "
                     f"{new_manifest.get('generator_version')}")
    # .get() so an older/partial manifest node can't KeyError.
    old_hashes = {n.get("id"): n.get("content_hash") for n in old.get("nodes", [])}
    new = {n["id"]: n["content_hash"] for n in new_manifest["nodes"]}
    for nid, h in new.items():
        if nid not in old_hashes:
            lines.append(f"added: {nid}")
        elif old_hashes[nid] != h:
            lines.append(f"changed: {nid}")
    for nid in old_hashes:
        if nid not in new:
            lines.append(f"removed: {nid}")
    lines.extend(f"stale file: {p}" for p in pruned)
    return lines


def _process_repo(repo: Path, args, generated_at: str) -> tuple[str, list[str], str]:
    """Derive one repo. Returns (status, detail_lines, adapter_name)."""
    adapter = adapters.select(repo, args.adapter)
    nodes = extract_all(adapter, repo)
    # --check never writes; it renders in dry-run to compute the new manifest + stale files.
    result = render(repo, nodes, adapter=adapter.name, generated_at=generated_at,
                    dry_run=args.dry_run or args.check)
    if args.check:
        drift = _drift(repo, result.manifest, result.pruned)
        return ("drift" if drift else "clean", drift, adapter.name)
    detail = [f"[{n.level.value}] {n.path} ({n.content_hash()})" for n in nodes]
    if result.pruned:
        detail.append(f"pruned {len(result.pruned)}: {', '.join(result.pruned)}")
    return (("planned" if args.dry_run else "written"), detail, adapter.name)


def _candidate_repos() -> list[Path]:
    base = dev_dir()
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


def _apply_repo(repo: Path, args) -> int:
    """Spec -> change brief: diff the edited on-disk model against derived code, emit instructions."""
    root = repo / MODEL_DIRNAME
    if not root.exists():
        print(f"error: no model at {root}\n"
              f"       run `uv run systemmodel {repo.name}` first, then edit the .md files and "
              f"re-run --apply.", file=sys.stderr)
        return 2
    try:
        adapter = adapters.select(repo, args.adapter)
        nodes = extract_all(adapter, repo)  # current state, in memory — never overwrites the spec
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: failed to derive {repo.name}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    brief = build_brief(repo, nodes)
    if brief is None:
        print(f"{repo.name}: code already matches the spec — nothing to apply.")
        return 0
    out = repo / "change-brief.md"
    out.write_text(brief, encoding="utf-8", newline="\n")
    print(brief)
    print(f"\n(wrote {out})")
    return 0


def _derive_platform(args, generated_at: str) -> int:
    """Aggregate platform signals across all repos into the L0 model in system-model/.systemmodel."""
    agg_kinds = aggregate_kinds()
    census: dict[str, list[str]] = {}
    records: list[tuple[str, dict]] = []
    specs: dict = {}
    adapters_used: set[str] = set()
    for repo in _candidate_repos():
        try:
            adapter = adapters.select(repo, args.adapter)
        except LookupError:
            continue
        classify = getattr(adapter, "classify", None)
        get_sigs = getattr(adapter, "platform_signals", None)
        get_specs = getattr(adapter, "platform_signal_specs", None)
        if not (callable(classify) and callable(get_sigs) and callable(get_specs)):
            continue
        try:
            kind = classify(repo)
            census.setdefault(kind, []).append(repo.name)
            if kind not in agg_kinds:
                continue  # not a service — excluded from invariant/convention aggregation
            sigs = get_sigs(repo)
            repo_specs = get_specs()
        except Exception as e:  # one bad repo shouldn't sink the aggregation
            print(f"  {repo.name}: ERROR {type(e).__name__}: {e}", file=sys.stderr)
            continue
        records.append((repo.name, sigs))
        for s in repo_specs:
            specs[s.key] = s
        adapters_used.add(adapter.name)

    if not records:
        print("no repos with platform-signal support found", file=sys.stderr)
        return 2

    # Provenance/counts reflect the repos actually aggregated (a service whose signals
    # raised is in the census but not in records).
    repos_used = sorted(r for r, _ in records)
    aggs = aggregate(records, specs, authored_signals())
    nodes = render_platform(aggs, census, agg_kinds, repos_used, adapters_used)
    root = platform_root()
    result = render(root, nodes, adapter="+".join(sorted(adapters_used)),
                    generated_at=generated_at, dry_run=args.dry_run or args.check)

    if args.check:
        drift = _drift(root, result.manifest, result.pruned)
        if drift:
            print(f"DRIFT - platform model stale vs {len(repos_used)} repos:")
            for line in drift:
                print(f"  {line}")
            return 1
        print(f"clean - platform model matches {len(repos_used)} repos")
        return 0

    verb = "would write" if args.dry_run else "wrote"
    print(f"platform model: {verb} under {root / MODEL_DIRNAME} "
          f"from {len(repos_used)} repos ({', '.join(sorted(adapters_used))})")
    for n in nodes:
        print(f"  [{n.level.value}] {n.path} ({n.content_hash()})")
    return 0


def main(argv: list[str] | None = None) -> int:
    # The model (and briefs) use Unicode (→, ⚠, ×); don't crash on a legacy console (Windows cp1252).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        prog="systemmodel", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", nargs="?", help="repo folder name (under DEV_DIR) or absolute path")
    parser.add_argument("--all", action="store_true", help="process every auto-detected repo in the container")
    parser.add_argument("--platform", action="store_true",
                        help="derive the L0 platform model into system-model/.systemmodel from all repos")
    parser.add_argument("--adapter", help="force a specific adapter instead of auto-detect")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    parser.add_argument("--check", action="store_true",
                        help="report drift vs the checked-in model without writing; exit 1 if stale")
    parser.add_argument("--apply", action="store_true",
                        help="spec -> code: diff the edited on-disk model against the code and emit "
                             "a change brief (does not edit code)")
    args = parser.parse_args(argv)

    if not args.all and not args.repo and not args.platform:
        parser.error("give a repo name, --all, or --platform")
    if args.platform and (args.repo or args.all):
        parser.error("--platform stands alone (not with a repo or --all)")
    if args.all and args.repo:
        parser.error("use either a repo name or --all, not both")
    if args.apply and (args.all or args.platform):
        parser.error("--apply works on a single repo (not with --all/--platform)")
    if args.apply and args.check:
        parser.error("--apply (spec->code) and --check (code->model) are opposite directions; use one")
    if args.apply and not args.repo:
        parser.error("--apply requires a repo name")

    generated_at = datetime.now().isoformat(timespec="seconds")

    if args.platform:
        return _derive_platform(args, generated_at)

    # ---- batch: --all ----
    if args.all:
        drifted = 0
        derived = skipped = errored = 0
        for repo in _candidate_repos():
            try:
                status, detail, _ = _process_repo(repo, args, generated_at)
            except LookupError:
                skipped += 1
                continue
            except Exception as e:  # keep the batch going; report the repo that failed
                errored += 1
                print(f"  {repo.name:24} ERROR {type(e).__name__}: {e}", file=sys.stderr)
                continue
            derived += 1
            if status == "drift":
                drifted += 1
                print(f"  {repo.name:24} DRIFT  {'; '.join(detail)}")
            else:
                print(f"  {repo.name:24} {status}")
        print(f"\n{derived} processed, {skipped} skipped (no adapter), {errored} errored"
              + (f", {drifted} drifted" if args.check else ""))
        # Fail the run on any error (a repo that couldn't be derived/verified) or, in
        # --check mode, on any drift — so CI can't go green on a partial/stale result.
        return 1 if (errored or (args.check and drifted)) else 0

    # ---- single repo ----
    try:
        repo = resolve_repo(args.repo)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not repo.exists():
        print(f"error: repo path does not exist: {repo}", file=sys.stderr)
        return 2

    if args.apply:
        return _apply_repo(repo, args)

    try:
        status, detail, adapter_name = _process_repo(repo, args, generated_at)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # surface any extractor failure as a clean error, not a traceback
        print(f"error: failed to derive {repo.name}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.check:
        if status == "drift":
            print(f"DRIFT - {repo.name}/.systemmodel is stale vs code:")
            for line in detail:
                print(f"  {line}")
            print("run: uv run systemmodel " + repo.name)
            return 1
        print(f"clean - {repo.name}/.systemmodel matches code")
        return 0

    verb = "would write" if args.dry_run else "wrote"
    print(f"adapter: {adapter_name}")
    print(f"target : {repo}")
    print(f"{verb} model under {repo / MODEL_DIRNAME}:")
    for line in detail:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
