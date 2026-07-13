"""`auto`: close the derive↔apply loop by driving an agent from the change brief.

`--apply` stops at emitting a change brief (`core/apply.py`) and hands it to a human. `auto`
takes the next step: it invokes the `claude` CLI on that brief so the agent edits the repo's
code, then re-derives and rebuilds the brief as the acceptance test. If drift remains it feeds
the residual gap back to a fresh agent run, up to a cap.

The core invariant is preserved: **system-model never edits code itself.** It orchestrates the
agent and verifies via re-derivation (code stays truth). This is the only module that shells
out, so the subprocess boundary lives here and `derive.py` stays a thin CLI.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from systemmodel.core.adapter import Adapter, extract_all
from systemmodel.core.apply import build_brief
from systemmodel.core.locate import model_root

_AGENT_FOOTER = (
    "\n\n---\n"
    "You are being run non-interactively to satisfy the change brief above. Edit ONLY this "
    "repo's code to close the gap. The system model is the spec, not the target — it lives in a "
    "standalone directory outside this repo, so do not attempt to edit it. Acceptance is "
    "`uv run systemmodel {repo} --check` reporting clean (re-derivation reproduces the spec)."
)


def _claude_cmd(dangerous: bool, model: str | None) -> list[str]:
    """The `claude` invocation (prompt is fed on stdin, so it's not in argv).

    `--verbose --output-format stream-json` makes headless (`-p`) mode emit each step as it
    happens (one JSON object per line) instead of buffering silently until the turn ends — so the
    user can watch the agent work. The output is raw JSON lines; pretty-printing is left to the
    caller's terminal.
    """
    cmd = ["claude", "-p", "--verbose", "--output-format", "stream-json"]
    cmd += (["--dangerously-skip-permissions"] if dangerous
            else ["--permission-mode", "acceptEdits"])
    if model:
        cmd += ["--model", model]
    return cmd


def _dirty_files(repo: Path) -> list[str] | None:
    """Uncommitted paths in `repo`, [] if clean, or None if it's not a git working tree."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None  # git not installed
    if proc.returncode != 0:
        return None  # not a git repo (or git error)
    return [line[3:] for line in proc.stdout.splitlines() if line.strip()]


def run_auto(repo: Path, adapter: Adapter, *, max_iters: int = 3, dangerous: bool = False,
             model: str | None = None, dry_run: bool = False, on_log=print) -> int:
    """Drive an agent from the change brief until re-derivation is clean (or the cap is hit).

    Returns a process exit code: 0 converged / nothing to do, 1 residual drift after the cap,
    2 a precondition failed (no model, dirty tree, claude missing).
    """
    root = model_root(repo)
    if not root.exists():
        on_log(f"error: no model at {root}\n"
               f"       run `uv run systemmodel {repo.name}` first, then edit the .md files and "
               f"re-run --auto.")
        return 2

    # Preconditions that only matter once we're actually going to mutate code.
    if not dry_run:
        if shutil.which("claude") is None:
            on_log("error: `claude` CLI not found on PATH — --auto needs it to edit the repo.")
            return 2
        dirty = _dirty_files(repo)
        if dirty is None:
            on_log(f"warning: {repo.name} is not a git working tree — agent edits won't be "
                   f"isolated or easily revertable.")
        elif dirty:
            on_log(f"error: {repo.name} has uncommitted changes; commit or stash them first so "
                   f"the agent's edits stay isolated:")
            for f in dirty:
                on_log(f"  {f}")
            return 2

    brief = build_brief(repo, extract_all(adapter, repo))
    if brief is None:
        on_log(f"{repo.name}: code already matches the spec — nothing to apply.")
        return 0

    cmd = _claude_cmd(dangerous, model)
    if dry_run:
        on_log(brief)
        on_log(f"\n--- dry run: would run `{' '.join(cmd)}` in {repo} with the brief on stdin "
               f"(up to {max_iters} iteration(s), re-deriving after each) ---")
        return 0

    out = root / "change-brief.md"
    for i in range(1, max_iters + 1):
        on_log(f"\n=== --auto iteration {i}/{max_iters} for {repo.name} ===")
        out.write_text(brief, encoding="utf-8", newline="\n")
        prompt = brief + _AGENT_FOOTER.format(repo=repo.name)
        try:
            # Inherit stdout/stderr so the user watches the agent; prompt goes on stdin to
            # dodge argv length limits on large briefs. Force utf-8 for stdin — the brief carries
            # → / ⚠ from the diffs and Windows' default cp1252 can't encode them.
            proc = subprocess.run(cmd, cwd=repo, input=prompt, text=True, encoding="utf-8")
        except FileNotFoundError:
            on_log("error: `claude` CLI not found on PATH.")
            return 2
        if proc.returncode != 0:
            on_log(f"error: claude exited {proc.returncode}; stopping. "
                   f"Brief left at {out} for inspection.")
            return 1

        # Acceptance: re-derive from the (now edited) code and see if the gap is closed.
        brief = build_brief(repo, extract_all(adapter, repo))
        if brief is None:
            out.unlink(missing_ok=True)
            on_log(f"\nclean — {repo.name} matches the spec after {i} iteration(s).")
            return 0
        on_log(f"residual drift after iteration {i}; feeding the gap back.")

    out.write_text(brief, encoding="utf-8", newline="\n")
    on_log(f"\nstill drifting after {max_iters} iteration(s); remaining gap left at {out}. "
           f"Inspect it, then re-run --auto or edit by hand.")
    return 1
