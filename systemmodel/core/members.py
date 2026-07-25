"""Splitting a brace-language type body into its members, for precise requirement anchoring.

Anchoring a requirement to a whole class is too blunt to be useful: changing one constant in a
seven-member service marked six of seven requirements stale, and a staleness signal that fires on
everything is one nobody reads. Splitting the body means a requirement about `MAX_PIXEL_LENGTH`
goes stale when that constant moves and stays quiet when a sibling method is edited.

Deliberately approximate. This is not a parser and does not need to be — it runs over comment-
stripped source and tracks brace and paren depth, which is enough for conventionally formatted
Groovy and Java. A member it fails to split simply stays coarse; nothing breaks.
"""
from __future__ import annotations

import re

from systemmodel.core.filters import significant_source

_TYPE_BODY = re.compile(r"\b(?:class|interface|enum|record|trait)\s+\w+[^{;]*\{")
_LEADING_ANNOTATIONS = re.compile(r"^(?:@\w+(?:\([^)]*\))?\s*)+")
_CALL_NAME = re.compile(r"(\w+)\s*\(")
_TRAILING_NAME = re.compile(r"(\w+)\s*$")
_NOT_A_MEMBER = {
    "if", "else", "for", "while", "switch", "try", "catch", "finally", "return",
    "new", "do", "synchronized", "static", "throw", "assert",
}


def _member_name(chunk: str) -> str | None:
    """The declared name of a member, from the text preceding its body or initializer.

    Order matters: the initializer is dropped before looking for a call, or a field like
    `TimelineService svc = new DateToPixelTimelineService()` would be named after the
    constructor on its right-hand side rather than the field on its left.
    """
    head = _LEADING_ANNOTATIONS.sub("", chunk.split("{", 1)[0].strip()).strip()
    declaration = head.split("=", 1)[0].strip().rstrip(";").strip()
    if not declaration:
        return None
    match = _CALL_NAME.search(declaration) or _TRAILING_NAME.search(declaration)
    if not match:
        return None
    name = match.group(1)
    return None if name in _NOT_A_MEMBER or name.isdigit() else name


def index_unique_members(facts: dict[str, dict]) -> None:
    """Also key unambiguous members by their bare name, in place.

    Anchors at feature grain are usually written `fillInGaps`, not
    `DateToPixelTimelineService.fillInGaps` — a reader naming one method rarely spells out its
    owner. Only names belonging to exactly one type are added: a bare `create` that three
    services all declare would resolve to an arbitrary one and report staleness for edits to a
    class the requirement never mentioned.
    """
    owners: dict[str, list[str]] = {}
    for key in facts:
        if "." in key:
            owners.setdefault(key.split(".", 1)[1], []).append(key)
    for bare, keys in owners.items():
        if len(keys) == 1 and bare not in facts:
            facts[bare] = facts[keys[0]]


def member_spans(source: str) -> dict[str, str]:
    """Member name -> its significant source, for the first type declared in the file.

    A name declared more than once (overloads) accumulates every span, so an anchor on the name
    covers all of them — the requirement did not distinguish, so neither should the hash.
    """
    text = significant_source(source)
    opening = _TYPE_BODY.search(text)
    if not opening:
        return {}

    spans: dict[str, str] = {}
    buffer: list[str] = []
    pending = ""  # annotations seen before the member they decorate
    depth, parens = 1, 0
    saw_body = False

    for ch in text[opening.end():]:
        if ch == "(":
            parens += 1
        elif ch == ")":
            parens = max(0, parens - 1)
        elif ch == "{":
            depth += 1
            saw_body = True
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break

        buffer.append(ch)

        # A member ends when its body closes, at a semicolon, or — for a Groovy field with no
        # semicolon — at a newline, but only once any parameter list is balanced.
        ends = (depth == 1 and saw_body and ch == "}") or \
               (depth == 1 and parens == 0 and ch in ";\n")
        if not ends:
            continue
        chunk = "".join(buffer).strip()
        buffer = []
        saw_body = False
        if not chunk:
            continue
        name = _member_name(chunk)
        if name:
            spans[name] = (spans.get(name, "") + "\n" + pending + chunk).strip()
            pending = ""
        elif chunk.startswith("@"):
            # An annotation on its own line terminates at the newline before its member is
            # seen. Carry it forward so `@Secure` counts as part of what the member is.
            pending += chunk + "\n"
        else:
            pending = ""

    return spans
