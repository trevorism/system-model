"""Resolve a target repo by name to a filesystem path.

Ported, self-contained version of the pattern in ai/agentism/tools/repo_paths.py so
this project stands alone. DEV_DIR defaults to the parent of this repo (a container of
sibling repos), overridable via the DEV_DIR environment variable.
"""
from __future__ import annotations

import os
from collections import deque
from pathlib import Path

_MAX_SEARCH_DEPTH = 8
_SKIPPED_DIR_NAMES = {
    ".git", ".venv", "node_modules", "__pycache__", "build", "dist", "target", ".gradle",
}


def dev_dir() -> Path:
    """Container directory holding sibling repos (this repo's parent by default)."""
    env = os.environ.get("DEV_DIR")
    if env:
        return Path(env)
    # locate.py -> core -> systemmodel -> <this repo> -> <container>
    return Path(__file__).resolve().parents[3]


def _iter_search_dirs(root: Path, max_depth: int = _MAX_SEARCH_DEPTH):
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    while queue:
        current, depth = queue.popleft()
        yield current
        if depth >= max_depth:
            continue
        try:
            for child in current.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if name.startswith(".") or name in _SKIPPED_DIR_NAMES:
                    continue
                queue.append((child, depth + 1))
        except PermissionError:
            continue


def find_repo(name: str, root: Path | None = None) -> Path | None:
    """Search the container recursively for a repository folder by name."""
    base = root or dev_dir()
    if not base.exists():
        return None
    direct = base / name
    if direct.is_dir():
        return direct
    for directory in _iter_search_dirs(base):
        if directory.name == name:
            return directory
    return None


def resolve_repo(repo_name: str) -> Path:
    """Resolve a repo from an absolute path or a folder name under the container."""
    if not repo_name or repo_name.strip() in (".", ".."):
        raise ValueError("repo_name must be a repo folder name or absolute path")
    p = Path(repo_name)
    if p.is_absolute():
        return p
    found = find_repo(repo_name)
    if found:
        return found
    raise FileNotFoundError(f"Could not locate repo '{repo_name}' under {dev_dir()}")
