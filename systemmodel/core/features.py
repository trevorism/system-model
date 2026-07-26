"""Features: the bottom of the hierarchy, and still semantic.

A feature is a capability with intent — "test-result fan-out", not "EventWebhookController has
three routes". It sits below the service overview and holds the finer-grained obligations that
would bury the ≤7 headline requirements if they all lived in one list.

Two things make this safe to generate.

**One call per repo.** The decomposition is a single agent request returning every feature and its
requirements, not a call per feature. Per-feature synthesis would multiply cost by the branching
factor for no extra fidelity, since the agent has to understand the whole repo either way.

**Sticky slugs.** Decomposition is non-deterministic, so a naive re-run would rename and re-cut
features on every pass and the tree would churn beyond recognition. A slug that exists on disk is
never renamed or deleted here; if a later decomposition stops proposing it, it stays and is marked
so a human can decide. That also keeps `--check` honest: the node set is derived from disk, so a
check never disagrees with a write about which documents should exist.
"""
from __future__ import annotations

import re

from systemmodel.core.evidence import stable_hash
from systemmodel.core.overlay import section_body
from systemmodel.core.requirements import (
    REQUIREMENTS_HEADING, Requirement, apply_hashes, hash_for, hydrate, merge, parse,
    render,
)
from systemmodel.core.schema import Level, Node

SUMMARY_HEADING = "Summary"

_DIR = "features"
# The separator may be an em/en dash or one or two hyphens — the prompt asks for `--`, but an
# agent writing prose reaches for `—` about as often.
_HEADING = re.compile(
    r"^##[ \t]+(?P<slug>[a-z0-9][a-z0-9-]*?)[ \t]+(?:[—–]|-{1,2})[ \t]+(?P<title>.+?)[ \t]*$"
    r"|^##[ \t]+(?P<bare>[a-z0-9][a-z0-9-]*)[ \t]*$",
    re.MULTILINE)
_UNPROPOSED = "> _No longer proposed by the latest decomposition — keep it or delete the file._"
_SLUG_SAFE = re.compile(r"[^a-z0-9-]+")


def slugify(text: str) -> str:
    return _SLUG_SAFE.sub("-", text.strip().lower()).strip("-")[:48]


class Feature:
    def __init__(self, slug: str, title: str, purpose: str,
                 requirements: list[Requirement] | None = None, proposed: bool = True):
        self.slug = slug
        self.title = title or slug
        self.purpose = purpose
        self.requirements = requirements or []
        self.proposed = proposed

    def __eq__(self, other):
        return isinstance(other, Feature) and self.__dict__ == other.__dict__

    def __repr__(self):
        return f"Feature({self.slug!r}, {len(self.requirements)} reqs, proposed={self.proposed})"

    @property
    def path(self) -> str:
        return f"{_DIR}/{self.slug}.md"

    def evidence(self, index: dict[str, dict]) -> str:
        """Hash of everything this feature's requirements rest on — its re-synthesis trigger."""
        return stable_hash(sorted(hash_for(r, index) for r in self.requirements))

    def summary(self) -> str:
        """The prose filling the Summary section: the human title, then what it is for."""
        lines = [f"**{self.title}**", ""]
        if not self.proposed:
            lines += [_UNPROPOSED, ""]
        if self.purpose:
            lines.append(self.purpose)
        return "\n".join(lines).strip()


def parse_body(text: str) -> Feature | None:
    """Recover a feature from a rendered document, so prior state can be preserved."""
    summary = section_body(text, SUMMARY_HEADING)
    requirements_text = section_body(text, REQUIREMENTS_HEADING)
    if summary is None and requirements_text is None:
        return None
    if summary is None:
        # Written before Summary existed: the title was an H1 and the purpose the prose under it.
        summary = "\n".join(_legacy_summary(text))
    title, purpose = "", ""
    for line in (summary or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped == _UNPROPOSED:
            continue
        if not title and stripped.startswith("**"):
            title = stripped.strip("*").strip()
        else:
            purpose = (purpose + " " + stripped).strip()
    return Feature(
        slug="", title=title, purpose=purpose,
        requirements=parse(requirements_text or ""),
        proposed=_UNPROPOSED not in (summary or ""),
    )


def _legacy_summary(text: str) -> list[str]:
    """Title and purpose from a document written before Summary existed.

    Frontmatter has to be skipped explicitly: it sits above the title and none of its lines look
    like a heading, so a naive scan folds `level: L2` and friends into the purpose.
    """
    lines: list[str] = []
    body = text.split("---\n", 2)[-1] if text.startswith("---") else text
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            lines.append(f"**{stripped[2:].strip()}**")
        elif stripped.startswith("## "):
            break
        elif stripped and not stripped.startswith("<!--"):
            lines.append(stripped)
    return lines


def parse_decomposition(text: str) -> list[Feature]:
    """Parse the agent's decomposition: `## slug — Title`, a purpose line, then `R1. …` prose."""
    features: list[Feature] = []
    headings = list(_HEADING.finditer(text))
    for position, heading in enumerate(headings):
        end = headings[position + 1].start() if position + 1 < len(headings) else len(text)
        chunk = text[heading.end():end]
        requirements = parse(chunk)
        purpose = ""
        for line in chunk.splitlines():
            stripped = line.strip()
            if stripped and not re.match(r"^R\d+\.", stripped) and not stripped.startswith(("->", "→")):
                purpose = stripped
                break
        raw_slug = heading.group("slug") or heading.group("bare") or ""
        slug = slugify(raw_slug)
        if slug:
            features.append(Feature(slug=slug, title=(heading.group("title") or slug).strip(),
                                    purpose=purpose, requirements=requirements))
    return features


def load(model_root, recorded: dict[str, dict] | None = None) -> dict[str, Feature]:
    """Features already on disk, keyed by slug. The node set is derived from this."""
    directory = model_root / _DIR
    if not directory.is_dir():
        return {}
    state = recorded or {}
    found: dict[str, Feature] = {}
    for path in sorted(directory.glob("*.md")):
        feature = parse_body(path.read_text(encoding="utf-8"))
        if feature:
            feature.slug = path.stem
            feature.requirements = hydrate(
                feature.requirements,
                state.get(feature.path, {}).get("requirements", {}))
            found[feature.slug] = feature
    return found


def reconcile(prior: dict[str, Feature], fresh: list[Feature],
              index: dict[str, dict]) -> list[Feature]:
    """Merge a fresh decomposition into what exists, preserving slugs and authored intent."""
    proposed = {f.slug for f in fresh}
    combined: dict[str, Feature] = {}

    for feature in fresh:
        existing = prior.get(feature.slug)
        requirements = merge(existing.requirements if existing else [], feature.requirements)
        combined[feature.slug] = Feature(
            slug=feature.slug, title=feature.title, purpose=feature.purpose,
            requirements=apply_hashes(requirements, index), proposed=True,
        )

    # A slug the decomposition dropped is kept and flagged rather than deleted: the agent may
    # simply have cut the repo differently this time, and silently discarding a human's promoted
    # requirements because of that would be the worst failure this layer could have.
    for slug, feature in prior.items():
        if slug not in proposed:
            combined[slug] = Feature(
                slug=slug, title=feature.title, purpose=feature.purpose,
                requirements=apply_hashes(feature.requirements, index), proposed=False,
            )
    return [combined[slug] for slug in sorted(combined)]


def nodes(features: list[Feature], index: dict[str, dict]) -> list[Node]:
    """One L2 node per feature: a stable heading skeleton the prose is poured into.

    The H1 is the slug, not the synthesized title — the slug is the identity, and keeping the
    title out of the skeleton means a reworded title is not drift. `synth_sections` records the
    evidence each section rests on, so a feature document goes stale exactly when the code its
    requirements rest on moves.
    """
    return [
        Node(Level.L2, "feature", f.slug, f.path,
             "\n".join([f"# Feature: {f.slug}", "",
                        f"## {SUMMARY_HEADING}", "",
                        f"## {REQUIREMENTS_HEADING}", ""]),
             synth_sections={SUMMARY_HEADING: f.evidence(index),
                             REQUIREMENTS_HEADING: f.evidence(index)})
        for f in features
    ]


def prose(features: list[Feature]) -> dict[str, dict[str, str]]:
    return {f.path: {SUMMARY_HEADING: f.summary(),
                     REQUIREMENTS_HEADING: render(f.requirements)} for f in features}


def requirement_hashes(features: list[Feature]) -> dict[str, dict[str, str]]:
    from systemmodel.core.requirements import hashes
    return {f.path: hashes(f.requirements) for f in features}
