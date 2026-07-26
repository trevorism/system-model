"""Intake: turn plain prose in `intent.md` into proper requirement records.

`intent.md` is the one file in a model that a human owns. Everything else is generated, so this is
where a change is asked for — in whatever words come naturally:

    ## Desired updates
    - POST /api/timeline must reject unauthenticated callers
    - R3 should also cover service accounts, not just human users
    - drop R5, that behaviour was removed last quarter
    - promote token-issuance R2

Intake reads those, works out what each one means, and does the filing: allocates the id, resolves
the anchors, and puts the record in the right document. That is the whole point — the failure this
replaces is a human hand-writing a record and putting it somewhere nothing maintains it, which is
a mistake easy enough to make that the author of this tool made it.

Anything intake applies becomes `authored`: a person asked for it, so it is binding intent, and
`state` resets to unverified because no verdict has been passed on the new wording yet.

Processed entries move to an `## Applied` log rather than vanishing. Someone wrote them; they
should be able to see what became of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from systemmodel.core.overlay import replace_section, section_body
from systemmodel.core.requirements import (
    AUTHORED, REQUIREMENTS_HEADING, UNVERIFIED, Requirement, parse, render,
)

INBOX_HEADING = "Desired updates"
APPLIED_HEADING = "Applied"

ADD, AMEND, RETIRE, PROMOTE = "add", "amend", "retire", "promote"
_ACTIONS = (ADD, AMEND, RETIRE, PROMOTE)

_BULLET = re.compile(r"^[-*][ \t]+(?P<text>.+)$")
_ENTRY = re.compile(r"^##[ \t]+ENTRY\b.*$", re.MULTILINE)
_FIELD = re.compile(r"^(?P<key>ACTION|DOCUMENT|TARGET|BODY|ANCHORS|NOTE):[ \t]*(?P<value>.*)$",
                    re.MULTILINE)


@dataclass
class Change:
    action: str
    document: str
    target: str | None = None
    body: str = ""
    anchors: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def valid(self) -> bool:
        if self.action not in _ACTIONS or not self.document:
            return False
        if self.action == ADD:
            return bool(self.body)
        return bool(self.target)


def read_inbox(text: str) -> list[str]:
    """The unprocessed entries: one per bullet, continuation lines folded in."""
    body = section_body(text, INBOX_HEADING)
    if not body:
        return []
    entries: list[str] = []
    for line in body.splitlines():
        bullet = _BULLET.match(line.strip())
        if bullet:
            entries.append(bullet.group("text").strip())
        elif line.strip() and entries:
            entries[-1] += " " + line.strip()
    return entries


def parse_plan(text: str) -> list[Change]:
    """Parse the agent's plan. Malformed entries are dropped rather than half-applied."""
    changes: list[Change] = []
    starts = [m.start() for m in _ENTRY.finditer(text)]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(text)
        fields = {m.group("key"): m.group("value").strip()
                  for m in _FIELD.finditer(text[start:end])}
        change = Change(
            action=fields.get("ACTION", "").lower(),
            document=fields.get("DOCUMENT", "").strip(),
            target=fields.get("TARGET") or None,
            body=fields.get("BODY", ""),
            anchors=[a.strip().strip("`") for a in fields.get("ANCHORS", "").split(",")
                     if a.strip()],
            note=fields.get("NOTE", ""),
        )
        if change.valid:
            changes.append(change)
    return changes


def _next_id(existing: list[Requirement]) -> str:
    return f"R{max((r.number for r in existing), default=0) + 1}"


def apply_change(records: list[Requirement], change: Change) -> tuple[list[Requirement], str]:
    """Apply one change to a document's records. Returns the new list and a log line."""
    by_id = {r.id: r for r in records}

    if change.action == ADD:
        fresh = Requirement(id=_next_id(records), body=change.body, anchors=change.anchors,
                            origin=AUTHORED, state=UNVERIFIED)
        return records + [fresh], f"added {fresh.id} to {change.document}"

    target = by_id.get(change.target or "")
    if target is None:
        return records, f"skipped — {change.document} has no {change.target}"

    if change.action == RETIRE:
        kept = [r for r in records if r.id != target.id]
        return kept, f"retired {target.id} from {change.document}"

    if change.action == PROMOTE:
        # Wording is unchanged, so an existing verdict still applies to the same claim.
        promoted = Requirement(id=target.id, body=target.body, anchors=target.anchors,
                               origin=AUTHORED, anchor_hash=target.anchor_hash,
                               state=target.state, finding=target.finding)
        return ([promoted if r.id == target.id else r for r in records],
                f"promoted {target.id} in {change.document} to authored")

    # AMEND: the claim itself changed, so any prior verdict is about different words.
    amended = Requirement(id=target.id, body=change.body or target.body,
                          anchors=change.anchors or target.anchors,
                          origin=AUTHORED, anchor_hash=target.anchor_hash,
                          state=UNVERIFIED)
    return ([amended if r.id == target.id else r for r in records],
            f"amended {target.id} in {change.document} (verdict cleared)")


def apply_to_document(text: str, changes: list[Change]) -> tuple[str, list[str]]:
    """Apply every change targeting one document, returning the new text and the log."""
    records = parse(section_body(text, REQUIREMENTS_HEADING) or "")
    log: list[str] = []
    for change in changes:
        records, line = apply_change(records, change)
        log.append(line)
    return replace_section(text, REQUIREMENTS_HEADING, render(records)), log


def clear_inbox(text: str, applied: list[str], stamp: str) -> str:
    """Move processed entries into the Applied log, so nothing a human wrote just disappears."""
    if not applied:
        return text
    previous = section_body(text, APPLIED_HEADING) or ""
    entry = "\n".join([f"### {stamp}"] + [f"- {line}" for line in applied])
    combined = (entry + "\n\n" + previous).strip() if previous.strip() else entry
    text = replace_section(text, INBOX_HEADING, "")
    if section_body(text, APPLIED_HEADING) is None:
        return text.rstrip() + f"\n\n## {APPLIED_HEADING}\n\n{combined}\n"
    return replace_section(text, APPLIED_HEADING, combined)
