"""Sections: the parts of a generated document that hold non-code-reconcilable content.

A model document is plain Markdown with `##` headings, and the tool owns some of those sections.
There is deliberately no marker syntax — nothing a reader has to be told to ignore, and nothing
that leaves them wondering whether a comment is load-bearing. A section is identified by its
heading, and it runs until the next heading at the same or a higher level.

Two kinds of section content are not derived from code and so are excluded from the content hash
(a prose edit must never read as drift):

  *synthesized* — written by an agent, regenerated when the facts beneath it move. Which facts,
  and their hash, is recorded in `MANIFEST.json` rather than in the prose.

  *authored* — written by a human in `intent.md`, never regenerated, never overwritten.

The skeleton used for hashing keeps the headings and blanks the bodies the tool does not derive,
so `--check` compares structure and derived facts while ignoring prose.
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)


def _slug(title: str) -> str:
    return title.strip().lower()


def section_spans(text: str) -> list[tuple[str, int, int, int]]:
    """`(title, level, body start, body end)` for every heading in the document."""
    headings = list(_HEADING.finditer(text))
    spans: list[tuple[str, int, int, int]] = []
    for position, heading in enumerate(headings):
        level = len(heading.group("hashes"))
        end = len(text)
        for later in headings[position + 1:]:
            if len(later.group("hashes")) <= level:
                end = later.start()
                break
        spans.append((heading.group("title").strip(), level, heading.end() + 1, end))
    return spans


def section_body(text: str, title: str) -> str | None:
    """The body under a `##`-level heading, or None when the document has no such section."""
    for found, level, start, end in section_spans(text):
        if level <= 2 and _slug(found) == _slug(title):
            return text[start:end].strip("\n")
    return None


def replace_section(text: str, title: str, body: str) -> str:
    """Swap the body under a heading, leaving the heading and the rest of the document alone."""
    for found, level, start, end in section_spans(text):
        if level <= 2 and _slug(found) == _slug(title):
            trailing = "\n\n" if end < len(text) else "\n"
            return text[:start] + body.strip("\n") + trailing + text[end:]
    return text


def blank_sections(text: str, titles) -> str:
    """The skeleton for hashing: same headings, bodies of the named sections emptied.

    Applied to both sides of every comparison, so prose that the tool does not derive — whether
    an agent wrote it or a person did — can change freely without registering as drift.
    """
    wanted = {_slug(t) for t in titles}
    for found, level, start, end in reversed(section_spans(text)):
        if level <= 2 and _slug(found) in wanted:
            text = text[:start] + text[end:]
    return text


def contains_heading_at_or_above(body: str, level: int) -> bool:
    """True if prose would break out of a section of the given level.

    Synthesis is told to emit a section body and nothing else. If it emits a heading anyway, the
    section silently ends early and everything after it is orphaned — a failure the old
    comment-delimited form could not have. Cheaper to reject than to debug.
    """
    return any(len(m.group("hashes")) <= level for m in _HEADING.finditer(body))
