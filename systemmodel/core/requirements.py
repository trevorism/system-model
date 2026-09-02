"""Requirements: the semantic unit of the model, and the thing a spec is made of.

A requirement states an obligation the system must meet, in a sentence a reader remembers, with
an anchor naming the code it rests on. It is deliberately *not* a description of structure: route
tables, dependency-injection lists and type signatures are all greppable, and a model made of them
costs attention without repaying it.

A record is plain Markdown, with nothing in it a reader has to be told to ignore:

    ### R1 — observed
    Listing is unauthenticated and therefore always reports the default bucket.
    → `ObjectController.listTables`, `DEFAULT_BUCKET_NAME`

    ### R2 — authored, verified
    Role is derived, never requested: apps get system; users get user, escalating to tenant
    admin or full admin only when the stored admin flag is set.
    → `AccessTokenService.getRoleForIdentity`, `User.admin`
    > Verified — derives every role claim server-side; no request model carries a role.

**Every record names its origin.** `observed` is what the code was found doing; `authored` is what
it has been told to do. Almost every record is observed, so the label is redundant to a reader who
already knows the default — and load-bearing for one who does not, which now includes agents
reading the model to plan a change. An unlabelled record read as binding intent turns incidental
behaviour into a constraint nobody chose. Machine state with no meaning to a reader — the anchor
hash — still lives in `MANIFEST.json` instead of cluttering the prose.

**Origin decides lifetime.** `derived` records are description, regenerated whenever synthesis
re-runs. `authored` records are binding intent and survive regeneration untouched. An authored id
is frozen at promotion and never reused, so a gate verdict can cite `R3` across runs; derived ids
are positional and get reallocated around the authored ones, because there is no stable identity
to preserve for prose that regenerates wholesale.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from systemmodel.core.evidence import stable_hash

REQUIREMENTS_HEADING = "Requirements"

AUTHORED = "authored"
DERIVED = "derived"
OBSERVED = "observed"

UNVERIFIED = "unverified"
VERIFIED = "verified"
VIOLATED = "violated"

NO_HASH = "-"

STALE = "stale"
UNANCHORED = "unanchored"

_HEADING = re.compile(r"^###[ \t]+(?P<id>R\d+)[ \t]*(?:[—–-]+[ \t]*(?P<status>.+?))?[ \t]*$",
                      re.MULTILINE)
_ANCHOR_LINE = re.compile(r"^[ \t]*(?:->|→)[ \t]*(?P<anchors>.+)$", re.MULTILINE)
_FINDING = re.compile(r"^>[ \t]*(?:Verified|Violated)[ \t]*[—–-]+[ \t]*(?P<finding>.+)$",
                      re.MULTILINE)
# A capitalised type, optionally qualified, OR a bare camelCase member. The latter needs an
# internal capital to qualify: feature-level anchors are routinely written as `fillInGaps` with
# no type prefix, but matching every lowercase word would turn ordinary prose into symbols.
_IDENTIFIER = re.compile(
    r"\b[A-Z][A-Za-z0-9]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b"
    r"|\b[a-z][a-zA-Z0-9]*[A-Z][A-Za-z0-9]*\b")

# Legacy shapes, kept only so an older model on disk can be migrated forward without a
# re-synthesis. Nothing writes either of these any more.
_LEGACY_BLOCK = re.compile(
    r"<!-- req:(?P<id>R\d+)(?P<attrs>[^>]*?)-->\n(?P<inner>.*?)\n<!-- /req -->", re.DOTALL)
_LEGACY_ATTR = re.compile(r"(\w+)=(\S+)")
_LEGACY_LEAD = re.compile(r"^\*\*R\d+\.\*\*[ \t]*")
_LEGACY_FINDING = re.compile(r"^>[ \t]*\*\*(?:verified|violated)\*\*[ \t]*—[ \t]*(?P<f>.+)$",
                             re.MULTILINE)
_LEGACY_PROSE = re.compile(r"^R(\d+)\.[ \t]*", re.MULTILINE)


@dataclass
class Requirement:
    id: str
    body: str
    anchors: list[str] = field(default_factory=list)
    origin: str = DERIVED
    anchor_hash: str = NO_HASH
    state: str = UNVERIFIED
    finding: str | None = None

    @property
    def number(self) -> int:
        return int(self.id[1:])

    @property
    def is_authored(self) -> bool:
        return self.origin == AUTHORED

    @property
    def needs_verification(self) -> bool:
        """Authored intent whose verdict is missing. Derived description is never verified.

        Verifying a synthesized description would be near-tautological — it was written from the
        code it would be checked against. Scoping to authored records means verification cost
        tracks how much intent you have committed to, not how large the model is.
        """
        return self.is_authored and self.state == UNVERIFIED

    def symbols(self) -> list[str]:
        """Identifier-shaped anchors, for resolution against an adapter's fact index.

        Anchors are written for humans, so alongside `TimelineController.generate` they carry
        prose ("header X-Correlation-ID"), string literals and repo lists. Resolution is
        best-effort by design: pull out what looks like a type or member and let the caller
        ignore what it cannot match, rather than forcing the author to write a formal reference.
        """
        found: list[str] = []
        for anchor in self.anchors:
            for token in _IDENTIFIER.findall(anchor):
                # Emit the qualified form and its owning type, so an anchor written at member
                # grain still resolves against an index that only knows the class.
                for candidate in (token, token.split(".", 1)[0]):
                    if candidate not in found:
                        found.append(candidate)
        return found

    def status_words(self) -> list[str]:
        """The annotations worth showing, origin first."""
        words = [OBSERVED if self.origin == DERIVED else self.origin]
        if self.state != UNVERIFIED:
            words.append(self.state)
        return words

    def render(self) -> str:
        lines = [f"### {self.id} — {', '.join(self.status_words())}", self.body]
        if self.anchors:
            lines.append("→ " + ", ".join(f"`{a}`" for a in self.anchors))
        if self.finding and self.state in (VERIFIED, VIOLATED):
            lines.append(f"> {self.state.capitalize()} — {self.finding}")
        return "\n".join(lines)


def _split_anchors(text: str) -> list[str]:
    """Split an anchor line on commas that aren't inside parentheses or quotes."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0 and quote is None:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    # Backticks are markdown decoration, not part of the reference.
    return [p for p in (q.replace("`", "").strip() for q in parts) if p]


def _normalize_body(text: str) -> str:
    """Collapse a wrapped prose block into one paragraph."""
    return " ".join(line.strip() for line in text.strip().splitlines() if line.strip())


def _parse_status(raw: str | None) -> tuple[str, str]:
    words = {w.strip().lower() for w in (raw or "").split(",") if w.strip()}
    origin = AUTHORED if AUTHORED in words else DERIVED
    state = VERIFIED if VERIFIED in words else VIOLATED if VIOLATED in words else UNVERIFIED
    return origin, state


def parse(text: str) -> list[Requirement]:
    """Parse records from a section body, tolerating both retired on-disk formats."""
    if "<!-- req:" in text:
        return _parse_legacy_blocks(text)
    headings = list(_HEADING.finditer(text))
    if not headings:
        return _parse_legacy_prose(text)
    found: list[Requirement] = []
    for position, heading in enumerate(headings):
        end = headings[position + 1].start() if position + 1 < len(headings) else len(text)
        chunk = text[heading.end():end]
        origin, state = _parse_status(heading.group("status"))
        finding_match = _FINDING.search(chunk)
        anchor_match = _ANCHOR_LINE.search(chunk)
        body_end = min(m.start() for m in (finding_match, anchor_match) if m) \
            if (finding_match or anchor_match) else len(chunk)
        found.append(Requirement(
            id=heading.group("id"),
            body=_normalize_body(chunk[:body_end]),
            anchors=_split_anchors(anchor_match.group("anchors")) if anchor_match else [],
            origin=origin,
            state=state,
            finding=finding_match.group("finding").strip() if finding_match else None,
        ))
    return found


def _parse_legacy_blocks(text: str) -> list[Requirement]:
    found: list[Requirement] = []
    for m in _LEGACY_BLOCK.finditer(text):
        attrs = dict(_LEGACY_ATTR.findall(m.group("attrs") or ""))
        inner = m.group("inner").strip()
        finding_match = _LEGACY_FINDING.search(inner)
        stripped = _LEGACY_LEAD.sub("", _LEGACY_FINDING.sub("", inner).strip())
        anchor_match = _ANCHOR_LINE.search(stripped)
        body = stripped[:anchor_match.start()] if anchor_match else stripped
        found.append(Requirement(
            id=m.group("id"), body=_normalize_body(body),
            anchors=_split_anchors(anchor_match.group("anchors")) if anchor_match else [],
            origin=attrs.get("origin", DERIVED),
            anchor_hash=attrs.get("anchors", NO_HASH),
            state=attrs.get("state", UNVERIFIED),
            finding=finding_match.group("f").strip() if finding_match else None,
        ))
    return found


def _parse_legacy_prose(text: str) -> list[Requirement]:
    pieces = _LEGACY_PROSE.split(text)
    if len(pieces) < 3:
        return []
    found: list[Requirement] = []
    for number, chunk in zip(pieces[1::2], pieces[2::2]):
        anchor_match = _ANCHOR_LINE.search(chunk)
        body = _normalize_body(chunk[:anchor_match.start()] if anchor_match else chunk)
        if body:
            found.append(Requirement(
                id=f"R{number}", body=body,
                anchors=_split_anchors(anchor_match.group("anchors")) if anchor_match else []))
    return found


def render(requirements: list[Requirement]) -> str:
    return "\n\n".join(r.render() for r in sorted(requirements, key=lambda r: r.number))


def hashes(requirements: list[Requirement]) -> dict[str, str]:
    """Anchor hashes for the manifest — the machine state kept out of the prose."""
    return {r.id: r.anchor_hash for r in requirements if r.anchor_hash != NO_HASH}


def hydrate(requirements: list[Requirement], recorded: dict[str, str]) -> list[Requirement]:
    """Restore anchor hashes from the manifest onto records parsed from Markdown."""
    return [replace(r, anchor_hash=recorded.get(r.id, r.anchor_hash)) for r in requirements]


def hash_for(requirement: Requirement, index: dict[str, dict]) -> str:
    """Hash the facts this requirement rests on, or `-` when none of its anchors resolve.

    Deliberately hashes extracted *facts* rather than file bytes: a reformatted comment or a
    reordered import must not mark an obligation for re-review, or the signal decays into noise
    and gets ignored — which is the failure mode this whole layer exists to avoid.
    """
    resolved = {name: index[name] for name in requirement.symbols() if name in index}
    # Where a member-grained anchor resolved, drop the whole-type fallback for that same type:
    # keeping both would make an edit anywhere in the class mark this requirement stale.
    refined = {name.split(".", 1)[0] for name in resolved if "." in name}
    kept = {name: facts for name, facts in resolved.items()
            if "." in name or name not in refined}
    return stable_hash(kept) if kept else NO_HASH


def apply_hashes(requirements: list[Requirement], index: dict[str, dict]) -> list[Requirement]:
    """Re-hash every requirement, demoting any verified one whose anchored code has moved.

    Without the demotion a re-derive would quietly re-baseline the hash and leave the record
    still claiming `verified` — so a change to the code underneath a binding guarantee would
    erase its own evidence.
    """
    updated: list[Requirement] = []
    for requirement in requirements:
        current = hash_for(requirement, index)
        # `-` means never hashed, not changed — recording a baseline for the first time must
        # not look like movement, or every record would be demoted the moment it is anchored.
        known = requirement.anchor_hash not in (NO_HASH, "")
        moved = known and current != requirement.anchor_hash
        demote = moved and requirement.state in (VERIFIED, VIOLATED)
        updated.append(replace(
            requirement, anchor_hash=current,
            state=UNVERIFIED if demote else requirement.state,
            # The finding described code that has since changed, so it is no longer evidence.
            finding=None if demote else requirement.finding,
        ))
    return updated


def staleness(requirements: list[Requirement],
              index: dict[str, dict]) -> list[tuple[Requirement, str]]:
    """Requirements whose anchored code moved, or that anchor nothing resolvable."""
    findings: list[tuple[Requirement, str]] = []
    for requirement in requirements:
        current = hash_for(requirement, index)
        if current == NO_HASH:
            findings.append((requirement, UNANCHORED))
        elif requirement.anchor_hash != current:
            findings.append((requirement, STALE))
    return findings


def merge(prior: list[Requirement], fresh: list[Requirement]) -> list[Requirement]:
    """Combine preserved authored intent with freshly synthesized description.

    Authored records are kept verbatim and keep their ids. Fresh derived records are renumbered
    into the numbers the authored ones do not hold. When synthesis produced nothing, prior
    derived records are kept so a failed or skipped run never silently empties the section.
    """
    authored = [r for r in prior if r.is_authored]
    if not fresh:
        return sorted(authored + [r for r in prior if not r.is_authored],
                      key=lambda r: r.number)

    # A fresh record restating a preserved authored one is dropped, not renumbered alongside it.
    # Synthesis is told not to restate, but a caller passing the prior records back in as `fresh`
    # would otherwise clone every promotion on every run — silently, and once per derive.
    held = {r.body.strip() for r in authored}
    fresh = [r for r in fresh if r.body.strip() not in held]

    taken = {r.number for r in authored}
    renumbered: list[Requirement] = []
    candidate = 1
    for requirement in fresh:
        while candidate in taken:
            candidate += 1
        taken.add(candidate)
        renumbered.append(replace(requirement, id=f"R{candidate}", origin=DERIVED))
        candidate += 1
    return sorted(authored + renumbered, key=lambda r: r.number)


def reconcile(prior: list[Requirement], fresh: list[Requirement] | None,
              index: dict[str, dict] | None = None) -> list[Requirement]:
    """Merge a fresh description into preserved intent and re-hash the result."""
    merged = merge(prior, fresh or [])
    return apply_hashes(merged, index) if index is not None else merged
