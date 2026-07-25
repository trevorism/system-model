"""Authored overlay: preserve human prose inside an otherwise-derived doc.

Some docs (currently `capabilities.md`) interleave deterministically *derived* content with
short *authored* narrative a human or agent writes. The derived part is code-truth and must stay
diff-stable; the authored part is intent that no code change can produce, so it must be preserved
across re-derivation and ignored by the reconciliation machinery (`--check`, `--apply`, `--gate`).

The mechanism is invisible HTML-comment anchors. Around each authored region:

    <!-- intent:<id> -->
    > intent: … human prose …
    <!-- /intent -->

An adapter emits these regions with a placeholder body; `derive` recovers the human-filled bodies
from the prior on-disk file and re-injects them (see core/render). This module is the single source
of truth for splitting a doc into (derived skeleton, authored regions) and merging them back — used
by both render (preserve) and apply (diff the skeleton only).
"""
from __future__ import annotations

import re

# The stub body an adapter emits for a capability with no authored intent yet. `split_authored`
# treats a region holding exactly this as "unfilled", so it isn't reported as real authored prose.
PLACEHOLDER = "> intent: _(unspecified)_"

# id charset covers capability ids like `event.testResult.submit`.
_REGION = re.compile(
    r"<!-- intent:(?P<id>[\w.\-:]+) -->\n(?P<inner>.*?)\n<!-- /intent -->",
    re.DOTALL,
)


def _canonical(region_id: str) -> str:
    """The placeholder form of a region — prose-independent, so skeletons compare structurally."""
    return f"<!-- intent:{region_id} -->\n{PLACEHOLDER}\n<!-- /intent -->"


def is_placeholder(inner: str) -> bool:
    return inner.strip() == PLACEHOLDER


def split_authored(text: str) -> tuple[str, dict[str, str]]:
    """Split a doc into its derived skeleton and its authored regions.

    Returns `(skeleton, authored)` where `skeleton` has every intent region normalized to the
    placeholder form (so two docs with the same structure but different prose yield identical
    skeletons — the basis for honest `--check`/`--apply` diffing), and `authored` maps each region
    id to its inner text (stripped). Placeholder/unfilled regions are omitted from `authored`.
    """
    authored: dict[str, str] = {}

    def _replace(m: re.Match) -> str:
        rid, inner = m.group("id"), m.group("inner").strip()
        if not is_placeholder(inner):
            authored[rid] = inner
        return _canonical(rid)

    skeleton = _REGION.sub(_replace, text)
    return skeleton, authored


def merge_authored(derived_body: str, authored: dict[str, str]) -> str:
    """Re-inject preserved authored regions into a freshly derived body.

    `derived_body` carries placeholder intent regions (as emitted by the adapter). For each region
    whose id has preserved prose in `authored`, replace the placeholder body with that prose;
    regions with no preserved prose keep the placeholder. Ids in `authored` that no longer appear
    in `derived_body` are dropped (their capability is gone) — the caller reports them.
    """
    def _replace(m: re.Match) -> str:
        rid = m.group("id")
        prose = authored.get(rid)
        if prose is None:
            return m.group(0)
        return f"<!-- intent:{rid} -->\n{prose}\n<!-- /intent -->"

    return _REGION.sub(_replace, derived_body)


def region_ids(text: str) -> set[str]:
    """The set of intent-region ids present in a doc."""
    return {m.group("id") for m in _REGION.finditer(text)}
