"""Source-file discovery filters.

Ported, self-contained version of ai/agentism/tools/discovery_filters.py. Adapters use
`iter_files` to walk a repo's relevant source/config while ignoring build output, VCS
metadata, and binaries.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

IGNORED_DIR_NAMES = {
    ".git", ".idea", "__pycache__", ".venv", "node_modules", ".gradle",
    "build", "dist", "target", ".pytest_cache", ".mypy_cache",
}

ALLOWED_EXTENSIONS = {
    ".groovy", ".gradle", ".java",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json",
    ".cs", ".csproj", ".sln", ".props", ".targets",
    ".py", ".pyi",
    ".toml", ".yaml", ".yml", ".xml", ".properties", ".md",
}

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf",
    ".zip", ".tar", ".gz", ".jar", ".class", ".exe", ".dll", ".so", ".dylib",
}

_IGNORED_DIRS_LOWER = {n.lower() for n in IGNORED_DIR_NAMES}
_ALLOWED_LOWER = {e.lower() for e in ALLOWED_EXTENSIONS}


def should_ignore_relative_path(relative_path: Path) -> bool:
    """True when a repo-relative path should be excluded from discovery."""
    parts = [p.lower() for p in relative_path.parts]
    if any(part in _IGNORED_DIRS_LOWER for part in parts[:-1]):
        return True
    suffix = relative_path.suffix.lower()
    if suffix in IGNORED_EXTENSIONS:
        return True
    return suffix not in _ALLOWED_LOWER


def iter_files(repo: Path, subdir: str | None = None) -> Iterator[Path]:
    """Yield absolute paths of relevant source/config files under repo[/subdir]."""
    base = repo / subdir if subdir else repo
    if not base.exists():
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if should_ignore_relative_path(rel):
            continue
        yield path


def read_text(path: Path) -> str:
    """Read a text file tolerantly (source may contain odd bytes)."""
    return path.read_text(encoding="utf-8", errors="replace")


def significant_source(text: str) -> str:
    """Source reduced to the lines that carry meaning, for change detection.

    Requirements anchored on a whole type need to notice a change *inside* it, which the
    structural facts alone are too coarse to see — a constant can move without any route,
    collaborator or signature changing. Hashing this instead of raw bytes keeps reformatting,
    blank lines and comment edits from marking an obligation for re-review, which is what would
    turn the staleness signal into noise.

    Whole-line comments only: stripping `//` mid-line would mangle URLs in string literals.
    """
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "*", "/*", "*/", "#")):
            continue
        kept.append(" ".join(line.split()))
    return "\n".join(kept)
