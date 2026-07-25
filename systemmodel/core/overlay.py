"""Overlay regions: content inside a derived doc that is not code-reconcilable.

Two kinds share one mechanism, both anchored by invisible HTML comments:

    <!-- intent:<id> -->            human narrative; preserved forever, never regenerated
    > intent: … prose …
    <!-- /intent -->

    <!-- synth:<id> evidence=<hash> -->   agent-synthesized prose; regenerated when the
    … prose …                             evidence hash moves, reused verbatim when it doesn't
    <!-- /synth -->

Neither kind is code-truth, so both are normalized away in the *skeleton* — the form used for
content hashing and for `--check` / `--apply` / `--gate` diffing. A prose edit is therefore never
reported as drift. A synth region's `evidence` attribute IS kept in the skeleton: it is derived
from code, so when the underlying facts move the doc legitimately counts as stale and the next
derive re-synthesizes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PLACEHOLDER = "> intent: _(unspecified)_"
SYNTH_PLACEHOLDER = "_(not yet synthesized)_"

_REGION = re.compile(
    r"<!-- intent:(?P<id>[\w.\-:]+) -->\n(?P<inner>.*?)\n<!-- /intent -->",
    re.DOTALL,
)

_SYNTH = re.compile(
    r"<!-- synth:(?P<id>[\w.\-:]+)(?:\s+evidence=(?P<evidence>[0-9a-f]*))?\s*-->\n"
    r"(?P<inner>.*?)\n<!-- /synth -->",
    re.DOTALL,
)


@dataclass(frozen=True)
class SynthRegion:
    evidence: str
    prose: str

    def is_placeholder(self) -> bool:
        return self.prose.strip() == SYNTH_PLACEHOLDER


def _canonical(region_id: str) -> str:
    return f"<!-- intent:{region_id} -->\n{PLACEHOLDER}\n<!-- /intent -->"


def _canonical_synth(region_id: str, evidence: str) -> str:
    anchor = f"<!-- synth:{region_id} evidence={evidence} -->" if evidence else f"<!-- synth:{region_id} -->"
    return f"{anchor}\n{SYNTH_PLACEHOLDER}\n<!-- /synth -->"


def synth_anchor(region_id: str, evidence: str) -> str:
    return _canonical_synth(region_id, evidence)


def is_placeholder(inner: str) -> bool:
    return inner.strip() == PLACEHOLDER


def split_regions(text: str) -> tuple[str, dict[str, str], dict[str, SynthRegion]]:
    authored: dict[str, str] = {}
    synthesized: dict[str, SynthRegion] = {}

    def _replace_intent(m: re.Match) -> str:
        rid, inner = m.group("id"), m.group("inner").strip()
        if not is_placeholder(inner):
            authored[rid] = inner
        return _canonical(rid)

    def _replace_synth(m: re.Match) -> str:
        rid = m.group("id")
        evidence = m.group("evidence") or ""
        region = SynthRegion(evidence=evidence, prose=m.group("inner").strip())
        if not region.is_placeholder():
            synthesized[rid] = region
        return _canonical_synth(rid, evidence)

    skeleton = _SYNTH.sub(_replace_synth, text)
    skeleton = _REGION.sub(_replace_intent, skeleton)
    return skeleton, authored, synthesized


def split_authored(text: str) -> tuple[str, dict[str, str]]:
    skeleton, authored, _ = split_regions(text)
    return skeleton, authored


def merge_authored(derived_body: str, authored: dict[str, str]) -> str:
    def _replace(m: re.Match) -> str:
        rid = m.group("id")
        prose = authored.get(rid)
        if prose is None:
            return m.group(0)
        return f"<!-- intent:{rid} -->\n{prose}\n<!-- /intent -->"

    return _REGION.sub(_replace, derived_body)


def merge_synth(derived_body: str, prose_by_id: dict[str, str]) -> str:
    def _replace(m: re.Match) -> str:
        rid = m.group("id")
        prose = prose_by_id.get(rid)
        if prose is None:
            return m.group(0)
        evidence = m.group("evidence") or ""
        anchor = f"<!-- synth:{rid} evidence={evidence} -->" if evidence else f"<!-- synth:{rid} -->"
        return f"{anchor}\n{prose}\n<!-- /synth -->"

    return _SYNTH.sub(_replace, derived_body)


def region_ids(text: str) -> set[str]:
    return {m.group("id") for m in _REGION.finditer(text)}


def synth_requests(text: str) -> dict[str, str]:
    return {m.group("id"): (m.group("evidence") or "") for m in _SYNTH.finditer(text)}
