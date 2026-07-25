"""CLI: derive a system model for a target repo and write it to the standalone model dir.

    uv run systemmodel <repo> [--dry-run] [--adapter NAME]   # -> $SYSTEMMODEL_DIR/<repo>/
    uv run systemmodel <repo> --check      # drift check, no writes, exit 1 if stale
    uv run systemmodel --all               # every auto-detected repo in the container
    uv run systemmodel --all --check       # platform-wide staleness check (CI)
    uv run systemmodel --platform          # L0 platform model -> $SYSTEMMODEL_DIR/ (root)
    uv run systemmodel <repo> --apply      # spec -> code: emit a change brief from the edited model
    uv run systemmodel <repo> --auto       # spec -> code: drive an agent from the brief, then verify
    uv run systemmodel <repo> --verify     # ask an agent whether the code meets each authored requirement
    uv run systemmodel <repo> --gate       # conformance: exit 1 if code violates authored intent
    uv run systemmodel --all --gate        # conformance across all repos (CI gate)
    uv run systemmodel --platform --gate   # conformance: exit 1 if platform.toml requirements violated

(equivalently: python -m systemmodel.derive ...)

System-agnostic: it selects an adapter, runs the extractors, and renders. Any stack
knowledge lives in the chosen adapter.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from systemmodel.core import adapter as adapters
from systemmodel.core.adapter import extract_all
from systemmodel.core.apply import authored_requirements, build_brief, requirement_gaps
from systemmodel.core.auto import run_auto
from systemmodel.core import features
from systemmodel.core.config import (
    acknowledged_exposure, aggregate_kinds, authored_exceptions, authored_signals,
)
from systemmodel.core.graph import service_graph
from systemmodel.core.locate import dev_dir, model_root, platform_model_root, resolve_repo
from systemmodel.core.platform import (
    aggregate, conformance, display_value as _disp, exception_lines, render_platform,
    trailing_conventions,
)
from systemmodel.core.render import read_manifest, render
from systemmodel.core.requirements import (
    UNANCHORED, VERIFIED, VIOLATED, Requirement, hash_for, parse_blocks, staleness,
    update_in_text,
)
from systemmodel.core.synth import decompose as synth_decompose
from systemmodel.core.synth import verify as synth_verify
from systemmodel.core.synth import resolve as synth_resolve


def _drift(mroot: Path, new_manifest: dict, pruned: list[str]) -> list[str]:
    """Human-readable drift lines comparing a freshly derived model to what's on disk."""
    old = read_manifest(mroot)
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


def _anchor_index(adapter, repo: Path) -> dict:
    """The adapter's symbol → facts index, or empty if it doesn't support one."""
    get_facts = getattr(adapter, "anchor_facts", None)
    if not callable(get_facts):
        return {}
    try:
        return get_facts(repo)
    except Exception:
        return {}  # a broken index degrades staleness tracking; it shouldn't sink the derive


def _synthesize(repo: Path, adapter, nodes: list, args) -> tuple[dict, list[str]]:
    """Resolve synthesized prose for a writing derive. Never called by --check/--gate/--dry-run,
    which reconcile the skeleton and so must stay free, offline and deterministic."""
    get_evidence = getattr(adapter, "extract_evidence", None)
    if not callable(get_evidence):
        return {}, []
    return synth_resolve(repo, nodes, get_evidence(repo), model=args.model,
                         anchor_index=_anchor_index(adapter, repo))


def _requirement_findings(repo: Path, adapter) -> tuple[list[str], int]:
    """(stale requirement lines, unanchored count) for the model on disk.

    Free and offline: it compares the anchor hash each requirement recorded against the one its
    anchors resolve to now. Once the structural docs stop being rendered this is the *only* thing
    that notices a code change, so it is what `--check` reports instead of a file-level hash.
    """
    root = model_root(repo)
    if not root.exists():
        return [], 0
    index = _anchor_index(adapter, repo)
    if not index:
        return [], 0
    stale_lines: list[str] = []
    unanchored = 0
    for path in sorted(root.rglob("*.md")):
        found = parse_blocks(path.read_text(encoding="utf-8"))
        if not found:
            continue
        rel = path.relative_to(root).as_posix()
        for requirement, reason in staleness(found, index):
            if reason == UNANCHORED:
                unanchored += 1
            else:
                stale_lines.append(f"{rel}:{requirement.id} stale (anchored code changed)")
    return stale_lines, unanchored


def _feature_layer(repo: Path, adapter, args, writing: bool) -> tuple[list, dict, list[str]]:
    """Feature nodes and their prose.

    When writing, the decomposition is resolved (one agent call, only if the code moved). When
    not, the node set is rebuilt from the documents already on disk — so `--check` and a write
    always agree on which files should exist, and a check never reports a feature as missing
    just because it did not run synthesis.
    """
    index = _anchor_index(adapter, repo)
    if not writing:
        existing = features.load(model_root(repo))
        ordered = [existing[slug] for slug in sorted(existing)]
        return features.nodes(ordered, index), {}, []

    get_evidence = getattr(adapter, "extract_evidence", None)
    if not callable(get_evidence):
        return [], {}, []
    resolved, stamp, regenerated = synth_decompose(
        repo, get_evidence(repo), index, model=args.model)
    return (features.nodes(resolved, index),
            features.prose(resolved, stamp),
            ["features"] if regenerated else [])


def _process_repo(repo: Path, args, generated_at: str) -> tuple[str, list[str], str]:
    """Derive one repo. Returns (status, detail_lines, adapter_name)."""
    adapter = adapters.select(repo, args.adapter)
    nodes = extract_all(adapter, repo)
    mroot = model_root(repo)
    writing = not (args.dry_run or args.check)
    prose, regenerated = _synthesize(repo, adapter, nodes, args) if writing else ({}, [])
    feature_nodes, feature_prose, feature_regen = _feature_layer(repo, adapter, args, writing)
    nodes = nodes + feature_nodes
    prose = {**prose, **feature_prose}
    regenerated = regenerated + feature_regen
    # --check never writes; it renders in dry-run to compute the new manifest + stale files.
    result = render(mroot, nodes, adapter=adapter.name, target=repo.name,
                    generated_at=generated_at, dry_run=not writing, synth_prose=prose)
    if args.check:
        drift = _drift(mroot, result.manifest, result.pruned)
        stale, unanchored = _requirement_findings(repo, adapter)
        detail = drift + stale
        if unanchored:
            detail.append(f"{unanchored} requirement(s) anchor nothing resolvable "
                          f"(not tracked; not a failure)")
        # Unanchored requirements are a coverage gap to improve, not a change to react to, so
        # they are reported without failing the check.
        return ("drift" if (drift or stale) else "clean", detail, adapter.name)
    detail = [f"[{n.level.value}] {n.path} ({n.content_hash()})" for n in nodes]
    if regenerated:
        detail.append(f"re-synthesized: {', '.join(regenerated)}")
    if result.pruned:
        detail.append(f"pruned {len(result.pruned)}: {', '.join(result.pruned)}")
    if result.dropped_authored:
        detail.append(f"dropped authored intent (capability gone): "
                      f"{', '.join(result.dropped_authored)}")
    return (("planned" if args.dry_run else "written"), detail, adapter.name)


def _candidate_repos() -> list[Path]:
    base = dev_dir()
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


def _gate_repo(repo: Path, args) -> list[str]:
    """Per-repo conformance: authored requirements the code does not meet.

    Empty means the repo conforms (nothing authored, or every obligation verified). Raises
    LookupError if no adapter matches, so the caller can decide between skipping and failing.
    """
    adapters.select(repo, args.adapter)  # keep the "no adapter" contract for the batch caller
    return [f"{requirement.id} ({path})" for path, requirement in requirement_gaps(repo)]


def _verification_targets(repo: Path, adapter) -> list[tuple[str, Requirement]]:
    """Authored requirements whose verdict is missing, negative, or no longer current.

    A violated record is re-checked rather than trusted: after an agent edits the code the whole
    point is to ask again. A verified one is re-checked when its anchors have moved, so a verdict
    never outlives the code it was made about.
    """
    index = _anchor_index(adapter, repo)
    targets: list[tuple[str, Requirement]] = []
    for path, requirement in authored_requirements(repo):
        moved = requirement.anchor_hash != hash_for(requirement, index)
        if requirement.state != VERIFIED or moved:
            targets.append((path, requirement))
    return targets


def _verify_repo(repo: Path, args) -> int:
    """Check each authored requirement against the code and record the verdict."""
    try:
        adapter = adapters.select(repo, args.adapter)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    root = model_root(repo)
    if not root.exists():
        print(f"error: no model at {root}; run `uv run systemmodel {repo.name}` first",
              file=sys.stderr)
        return 2

    targets = _verification_targets(repo, adapter)
    if not targets:
        total = len(authored_requirements(repo))
        print(f"{repo.name}: nothing to verify "
              f"({total} authored requirement(s), all verified against current code)"
              if total else
              f"{repo.name}: no authored requirements yet — promote one with `origin=authored`")
        return 0

    updates: dict[str, list[Requirement]] = {}
    violated = unclear = 0
    for path, requirement in targets:
        print(f"  verifying {path}:{requirement.id} …")
        state, finding = synth_verify(repo, requirement, model=args.model)
        if state is None:
            unclear += 1
            print(f"    unclear — leaving {requirement.id} as {requirement.state}"
                  + (f": {finding}" if finding else ""))
            continue
        if state == VIOLATED:
            violated += 1
        print(f"    {state}" + (f" — {finding}" if finding else ""))
        updates.setdefault(path, []).append(
            replace(requirement, state=state, finding=finding))

    for path, requirements in updates.items():
        target = root / path
        target.write_text(update_in_text(target.read_text(encoding="utf-8"), requirements),
                          encoding="utf-8", newline="\n")

    checked = sum(len(v) for v in updates.values())
    print(f"\n{repo.name}: {checked} verified, {violated} violated, {unclear} unclear")
    return 1 if violated else 0


def _platform_aggregates(args) -> dict:
    """Aggregate platform signals across services, for advisory comparison against one repo."""
    agg_kinds = aggregate_kinds()
    records: list[tuple[str, dict]] = []
    specs: dict = {}
    for candidate in _candidate_repos():
        try:
            adapter = adapters.select(candidate, args.adapter)
        except LookupError:
            continue
        classify = getattr(adapter, "classify", None)
        get_sigs = getattr(adapter, "platform_signals", None)
        get_specs = getattr(adapter, "platform_signal_specs", None)
        if not (callable(classify) and callable(get_sigs) and callable(get_specs)):
            continue
        try:
            if classify(candidate) not in agg_kinds:
                continue
            records.append((candidate.name, get_sigs(candidate)))
            for spec in get_specs():
                specs[spec.key] = spec
        except Exception:
            continue
    if not records:
        return {}
    return aggregate(records, specs, authored_signals(), authored_exceptions())


def _advisories(repo: Path, args) -> list[str]:
    try:
        aggs = _platform_aggregates(args)
    except Exception:
        return []
    return [f"**{label}:** `{_disp(value)}` — most services are on `{_disp(norm)}`"
            for label, value, norm in trailing_conventions(repo.name, aggs)]


def _apply_repo(repo: Path, args) -> int:
    """Intent -> change brief: the authored requirements the code does not meet."""
    root = model_root(repo)
    if not root.exists():
        print(f"error: no model at {root}\n"
              f"       run `uv run systemmodel {repo.name}` first, then promote a requirement to "
              f"`origin=authored` and re-run --apply.", file=sys.stderr)
        return 2
    try:
        adapters.select(repo, args.adapter)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    brief = build_brief(repo, advisories=_advisories(repo, args))
    if brief is None:
        print(f"{repo.name}: every authored requirement is verified — nothing to apply.")
        return 0
    out = root / "change-brief.md"
    out.write_text(brief, encoding="utf-8", newline="\n")
    print(brief)
    print(f"\n(wrote {out})")
    return 0


def _derive_platform(args, generated_at: str) -> int:
    """Aggregate platform signals across all repos into the L0 model at the standalone root."""
    agg_kinds = aggregate_kinds()
    census: dict[str, list[str]] = {}
    records: list[tuple[str, dict]] = []
    cap_summaries: list[tuple[str, dict]] = []
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
            # Exposure is scanned across every repo, not just services: an unauthenticated write
            # in a tester or an experiment is still an unauthenticated write, and scoping the
            # security list to one repo kind hides whole classes of repo from it.
            get_caps = getattr(adapter, "capability_summary", None)
            if callable(get_caps):
                summary = get_caps(repo)
                if summary is not None:
                    summary["kind"] = kind
                    cap_summaries.append((repo.name, summary))
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
    aggs = aggregate(records, specs, authored_signals(), authored_exceptions())

    # Conformance gate: measure code against authored platform.toml requirements; write nothing.
    if args.gate:
        conf = conformance(aggs)
        excused = exception_lines(conf)
        if not conf.required:
            print(f"clean - no authored requirements in platform.toml (nothing to gate); "
                  f"scanned {len(repos_used)} repos")
            return 0
        if not conf.violating_signals:
            print(f"clean - all {len(conf.required)} authored requirement(s) hold across "
                  f"{len(repos_used)} repos"
                  + (f", with {len(excused)} authored exception(s):" if excused else ""))
            for line in excused:
                print(f"  excepted: {line}")
            return 0
        print(f"VIOLATION - {len(conf.violating_signals)} authored requirement(s) violated "
              f"across {len(conf.repos_in_violation)} repo(s):")
        for a in conf.violating_signals:
            violators = ", ".join(f"{r}=`{_disp(v)}`" for r, v in a.violators())
            print(f"  {a.spec.label}: REQUIRED `{_disp(a.authored)}` — {violators}")
        for line in excused:
            print(f"  excepted: {line}")
        print(f"repos in violation: {', '.join(conf.repos_in_violation)}")
        print("run: uv run systemmodel --platform   (for the full report)")
        return 1

    nodes = render_platform(aggs, census, agg_kinds, repos_used, adapters_used,
                            graph=service_graph(), exposure=cap_summaries,
                            acknowledged=acknowledged_exposure())
    root = platform_model_root()
    result = render(root, nodes, adapter="+".join(sorted(adapters_used)), target="platform",
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
    print(f"platform model: {verb} under {root} "
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
                        help="derive the L0 platform model into the standalone model root from all repos")
    parser.add_argument("--adapter", help="force a specific adapter instead of auto-detect")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    parser.add_argument("--check", action="store_true",
                        help="report drift vs the checked-in model without writing; exit 1 if stale")
    parser.add_argument("--verify", action="store_true",
                        help="check each authored requirement against the code with an agent and "
                             "record the verdict; exit 1 if any is violated")
    parser.add_argument("--gate", action="store_true",
                        help="conformance gate: exit 1 if code violates authored intent — a repo's "
                             "edited spec (repo/--all) or platform.toml requirements (--platform). "
                             "Report only; writes nothing")
    parser.add_argument("--apply", action="store_true",
                        help="spec -> code: diff the edited on-disk model against the code and emit "
                             "a change brief (does not edit code)")
    parser.add_argument("--auto", action="store_true",
                        help="spec -> code: drive the claude CLI from the change brief to edit the "
                             "repo, then re-derive to verify (loops until --check clean)")
    parser.add_argument("--max-iters", type=int, default=3,
                        help="--auto: max agent iterations before giving up (default 3)")
    parser.add_argument("--dangerous", action="store_true",
                        help="--auto: run the agent with --dangerously-skip-permissions instead of "
                             "acceptEdits (lets it run Bash/tests, but can run anything)")
    parser.add_argument("--model", help="--auto: model for the spawned claude agent")
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
    if args.auto and (args.all or args.platform):
        parser.error("--auto works on a single repo (not with --all/--platform)")
    if args.auto and args.check:
        parser.error("--auto (spec->code) and --check (code->model) are opposite directions; use one")
    if args.auto and args.apply:
        parser.error("--auto drives an agent from the brief; --apply only emits it — use one")
    if args.auto and not args.repo:
        parser.error("--auto requires a repo name")
    if args.gate and args.check:
        parser.error("--check (staleness) and --gate (conformance) are separate checks; run each")
    if args.verify and (args.all or args.platform):
        parser.error("--verify works on a single repo (agent calls are per requirement)")
    if args.verify and not args.repo:
        parser.error("--verify requires a repo name")
    if args.verify and (args.check or args.gate or args.apply or args.auto):
        parser.error("--verify records verdicts; --check/--gate/--apply/--auto read them — run each")
    if args.gate and args.apply:
        parser.error("--apply emits the change brief; --gate checks the same gap with an exit code — use one")
    if args.gate and args.auto:
        parser.error("--auto drives an agent to close the gap; --gate only checks it — use one")

    generated_at = datetime.now().isoformat(timespec="seconds")

    if args.platform:
        return _derive_platform(args, generated_at)

    # ---- batch: --all --gate ----
    if args.all and args.gate:
        violated = skipped = errored = checked = 0
        for repo in _candidate_repos():
            try:
                paths = _gate_repo(repo, args)
            except LookupError:
                skipped += 1
                continue
            except Exception as e:  # keep the batch going; report the repo that failed
                errored += 1
                print(f"  {repo.name:24} ERROR {type(e).__name__}: {e}", file=sys.stderr)
                continue
            checked += 1
            if paths:
                violated += 1
                print(f"  {repo.name:24} VIOLATION  unmet: {', '.join(paths)}")
            else:
                print(f"  {repo.name:24} ok")
        print(f"\n{checked} checked, {skipped} skipped (no adapter), {errored} errored, "
              f"{violated} in violation")
        return 1 if (violated or errored) else 0

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

    if args.verify:
        return _verify_repo(repo, args)

    if args.gate:
        try:
            paths = _gate_repo(repo, args)
        except LookupError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if paths:
            print(f"VIOLATION - {repo.name}: unmet authored requirement(s): {', '.join(paths)}")
            print(f"run: uv run systemmodel {repo.name} --apply   (for the change brief)")
            return 1
        print(f"clean - {repo.name} meets every authored requirement")
        return 0

    if args.apply:
        return _apply_repo(repo, args)

    if args.auto:
        try:
            adapter = adapters.select(repo, args.adapter)
        except LookupError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return run_auto(repo, adapter, max_iters=args.max_iters, dangerous=args.dangerous,
                        model=args.model, dry_run=args.dry_run)

    try:
        status, detail, adapter_name = _process_repo(repo, args, generated_at)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # surface any extractor failure as a clean error, not a traceback
        print(f"error: failed to derive {repo.name}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    mroot = model_root(repo)
    if args.check:
        if status == "drift":
            print(f"DRIFT - {mroot} is stale vs code:")
            for line in detail:
                print(f"  {line}")
            print("run: uv run systemmodel " + repo.name)
            return 1
        print(f"clean - {mroot} matches code")
        return 0

    verb = "would write" if args.dry_run else "wrote"
    print(f"adapter: {adapter_name}")
    print(f"target : {repo}")
    print(f"{verb} model under {mroot}:")
    for line in detail:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
