"""Requirements: the semantic unit of the model, and the thing a spec is made of.

A requirement states an obligation the system must meet, in a sentence a reader remembers, with
an anchor naming the code it rests on. It is deliberately *not* a description of structure: route
tables, dependency-injection lists and type signatures are all greppable, and a model made of them
costs attention without repaying it.

Each record carries machine-written metadata in an HTML comment, the same mechanism
`core/overlay.py` uses for `intent:` and `synth:` regions:

    <!-- req:R3 origin=authored anchors=4a1c2f09 state=verified -->
    **R3.** Mutating endpoints require a signed app token, so a caller cannot reach data its own
    credentials do not permit.
    → `TimelineController.generate`, `SecureHttpClient`
    <!-- /req -->

Nothing in the header is hand-typed. A human edits the prose and flips `origin` to `authored`;
the tool owns `anchors` and `state`.

**Origin decides lifetime.** `derived` records are descriptive — regenerated whenever synthesis
re-runs. `authored` records are binding intent and survive regeneration untouched.

**ID stability follows from that.** An authored ID is frozen at promotion and never reused, so a
gate verdict or a change brief can reference it across runs. Derived IDs are positional and are
reallocated on each re-synthesis into whatever numbers the authored ones aren't holding — there is
no stable identity to preserve for prose that regenerates wholesale, and pretending otherwise
would be a lie the reader could not check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from systemmodel.core.evidence import stable_hash

# Synth regions with this id carry requirements, in any document. Phase 4's per-feature docs
# reuse the same id, so the reconciliation below applies to them without further plumbing.
REQUIREMENTS_REGION = "requirements"

AUTHORED = "authored"
DERIVED = "derived"

UNVERIFIED = "unverified"
VERIFIED = "verified"
VIOLATED = "violated"

NO_HASH = "-"

_BLOCK = re.compile(
    r"<!-- req:(?P<id>R\d+)(?P<attrs>[^>]*?)-->\n(?P<inner>.*?)\n<!-- /req -->",
    re.DOTALL,
)
_ATTR = re.compile(r"(\w+)=(\S+)")
_LEGACY_SPLIT = re.compile(r"^R(\d+)\.[ \t]*", re.MULTILINE)
_ANCHOR_LINE = re.compile(r"^[ \t]*(?:->|→)[ \t]*(?P<anchors>.+)$", re.MULTILINE)
_LEAD = re.compile(r"^\*\*R\d+\.\*\*[ \t]*")
_FINDING = re.compile(r"^>[ \t]*\*\*(?:verified|violated)\*\*[ \t]*—[ \t]*(?P<finding>.+)$",
                      re.MULTILINE)
# A capitalised type, optionally qualified, OR a bare camelCase member. The latter needs an
# internal capital to qualify: feature-level anchors are routinely written as `fillInGaps` with
# no type prefix, but matching every lowercase word would turn ordinary prose into symbols.
_IDENTIFIER = re.compile(
    r"\b[A-Z][A-Za-z0-9]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b"
    r"|\b[a-z][a-zA-Z0-9]*[A-Z][A-Za-z0-9]*\b")


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
    def needs_verification(self) -> bool:
        """Authored intent whose verdict is missing. Derived description is never verified.

        Verifying a synthesized description would be near-tautological — it was written from the
        code it would be checked against. Scoping to authored records means verification cost
        tracks how much intent you have committed to, not how large the model is.
        """
        return self.is_authored and self.state == UNVERIFIED

    @property
    def number(self) -> int:
        return int(self.id[1:])

    @property
    def is_authored(self) -> bool:
        return self.origin == AUTHORED

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
                # grain still resolves against an index that only knows the class. Anchors are
                # prose; the index should meet them where they are rather than the reverse.
                for candidate in (token, token.split(".", 1)[0]):
                    if candidate not in found:
                        found.append(candidate)
        return found

    def render(self) -> str:
        header = (f"<!-- req:{self.id} origin={self.origin} "
                  f"anchors={self.anchor_hash or NO_HASH} state={self.state} -->")
        lines = [header, f"**{self.id}.** {self.body}".rstrip()]
        if self.anchors:
            lines.append("→ " + ", ".join(f"`{a}`" for a in self.anchors))
        if self.finding and self.state in (VERIFIED, VIOLATED):
            lines.append(f"> **{self.state}** — {self.finding}")
        lines.append("<!-- /req -->")
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
    # Backticks are markdown decoration, not part of the reference. Stripping all of them (not
    # just a wrapping pair) keeps an anchor like ``Timeline.vue `item.employer`­`` from rendering
    # as broken nested code spans when it is re-emitted.
    cleaned = (p.replace("`", "").strip() for p in parts)
    return [p for p in cleaned if p]


def _split_body_and_anchors(chunk: str) -> tuple[str, list[str]]:
    m = _ANCHOR_LINE.search(chunk)
    if not m:
        return _normalize_body(chunk), []
    body = chunk[:m.start()]
    return _normalize_body(body), _split_anchors(m.group("anchors"))


def _normalize_body(text: str) -> str:
    """Collapse a wrapped, indented prose block into one paragraph."""
    return " ".join(line.strip() for line in text.strip().splitlines() if line.strip())


def parse_blocks(text: str) -> list[Requirement]:
    """Requirements written in this module's own block format."""
    found: list[Requirement] = []
    for m in _BLOCK.finditer(text):
        attrs = dict(_ATTR.findall(m.group("attrs") or ""))
        inner = m.group("inner").strip()
        finding_match = _FINDING.search(inner)
        finding = finding_match.group("finding").strip() if finding_match else None
        body, anchors = _split_body_and_anchors(
            _LEAD.sub("", _FINDING.sub("", inner).strip()))
        found.append(Requirement(
            id=m.group("id"),
            body=body,
            anchors=anchors,
            origin=attrs.get("origin", DERIVED),
            anchor_hash=attrs.get("anchors", NO_HASH),
            state=attrs.get("state", UNVERIFIED),
            finding=finding,
        ))
    return found


def parse_legacy(text: str) -> list[Requirement]:
    """Requirements in the prose format synthesis has emitted so far (`R1. …` / `    -> …`).

    This is the bootstrap path: 377 requirements already exist across the estate in this shape,
    and they are good. Re-deriving them from scratch would discard synthesis work already paid
    for and, worse, churn prose a human may already have come to rely on.
    """
    pieces = _LEGACY_SPLIT.split(text)
    if len(pieces) < 3:
        return []
    found: list[Requirement] = []
    for number, chunk in zip(pieces[1::2], pieces[2::2]):
        body, anchors = _split_body_and_anchors(chunk)
        if body:
            found.append(Requirement(id=f"R{number}", body=body, anchors=anchors))
    return found


def parse(text: str) -> list[Requirement]:
    """Parse whichever format the text is in — blocks if present, else legacy prose."""
    blocks = parse_blocks(text)
    return blocks if blocks else parse_legacy(text)


def update_in_text(text: str, updated: list[Requirement]) -> str:
    """Rewrite matching requirement blocks in place, leaving everything else untouched.

    Verification edits a verdict inside a document that also holds synthesized prose and possibly
    hand-written intent; re-rendering the whole file would put both at risk for the sake of one
    attribute.
    """
    by_id = {r.id: r for r in updated}

    def replace_block(match: re.Match) -> str:
        requirement = by_id.get(match.group("id"))
        return requirement.render() if requirement else match.group(0)

    return _BLOCK.sub(replace_block, text)


def render(requirements: list[Requirement]) -> str:
    return "\n\n".join(r.render() for r in sorted(requirements, key=lambda r: r.number))


STALE = "stale"
UNANCHORED = "unanchored"


def hash_for(requirement: Requirement, index: dict[str, dict]) -> str:
    """Hash the facts this requirement rests on, or `-` when none of its anchors resolve.

    Deliberately hashes extracted *facts* rather than file bytes: a reformatted comment or a
    reordered import must not mark an obligation for re-review, or the signal decays into noise
    and gets ignored — which is the failure mode this whole layer exists to avoid.
    """
    resolved = {name: index[name] for name in requirement.symbols() if name in index}
    # Where a member-grained anchor resolved, drop the whole-type fallback for that same type:
    # keeping both would make an edit anywhere in the class mark this requirement stale, which
    # is the coarseness the member index exists to remove. Anchors naming other types are kept.
    refined = {name.split(".", 1)[0] for name in resolved if "." in name}
    kept = {name: facts for name, facts in resolved.items()
            if "." in name or name not in refined}
    return stable_hash(kept) if kept else NO_HASH


def apply_hashes(requirements: list[Requirement], index: dict[str, dict]) -> list[Requirement]:
    """Re-hash every requirement, demoting any verified one whose anchored code has moved.

    Without the demotion a re-derive would quietly re-baseline the hash and leave the record
    still claiming `verified` — so a change to the code underneath a binding guarantee would
    erase its own evidence. Rebaselining is fine; rebaselining while keeping the old verdict is
    not.
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
            requirement,
            anchor_hash=current,
            state=UNVERIFIED if demote else requirement.state,
            # The finding described code that has since changed, so it is no longer evidence
            # of anything. Keeping it would let a stale verdict masquerade as a current one.
            finding=None if demote else requirement.finding,
        ))
    return updated


def staleness(requirements: list[Requirement],
              index: dict[str, dict]) -> list[tuple[Requirement, str]]:
    """Requirements whose anchored code moved, or that anchor nothing resolvable.

    `stale` means the obligation needs re-verification: the code beneath it changed, and whether
    it still holds is now an open question. `unanchored` means it can never go stale, because
    nothing it names could be resolved — worth surfacing separately, since a requirement nobody
    can track is a gap in coverage rather than a change.
    """
    findings: list[tuple[Requirement, str]] = []
    for requirement in requirements:
        current = hash_for(requirement, index)
        if current == NO_HASH:
            findings.append((requirement, UNANCHORED))
        elif requirement.anchor_hash != current:
            findings.append((requirement, STALE))
    return findings


def reconcile(prior_text: str, fresh_text: str | None = None,
              index: dict[str, dict] | None = None) -> str:
    """The text a requirements region should hold, given what was there and what synthesis made.

    Called on every run, not only when synthesis fires, because it does double duty: with
    `fresh_text` it merges new description into preserved intent, and without it, it normalizes
    whatever is already on disk. That second case is what migrates the legacy `R1. …` prose into
    records — a repo whose evidence never moves would otherwise never be converted.

    Falls back to the original text when there is nothing parseable, so a region holding
    something unexpected is left alone rather than blanked.
    """
    prior = parse(prior_text) if prior_text else []
    fresh = parse(fresh_text) if fresh_text else []
    if not prior and not fresh:
        return fresh_text if fresh_text is not None else prior_text
    merged = merge(prior, fresh)
    if index is not None:
        merged = apply_hashes(merged, index)
    return render(merged)


def merge(prior: list[Requirement], fresh: list[Requirement]) -> list[Requirement]:
    """Combine preserved authored intent with freshly synthesized description.

    Authored records are kept verbatim and keep their IDs. Fresh derived records are renumbered
    into the numbers the authored ones do not hold, in the order synthesis produced them. When
    synthesis produced nothing, prior derived records are kept so a failed or skipped run never
    silently empties the section.
    """
    authored = [r for r in prior if r.is_authored]
    if not fresh:
        return sorted(authored + [r for r in prior if not r.is_authored],
                      key=lambda r: r.number)

    taken = {r.number for r in authored}
    renumbered: list[Requirement] = []
    candidate = 1
    for requirement in fresh:
        while candidate in taken:
            candidate += 1
        taken.add(candidate)
        renumbered.append(Requirement(
            id=f"R{candidate}",
            body=requirement.body,
            anchors=requirement.anchors,
            origin=DERIVED,
            anchor_hash=requirement.anchor_hash,
            state=requirement.state,
        ))
        candidate += 1
    return sorted(authored + renumbered, key=lambda r: r.number)
