"""Evidence: the deterministic facts a synthesis pass reasons over.

An adapter extracts an `Evidence` bundle whose keys are *sections* corresponding 1:1 to the
`synth:` regions in a doc. Each section hashes independently, so re-synthesis is scoped to the
regions whose underlying facts actually moved — which is both the cost control and the change
stream.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class Evidence:
    target: str
    sections: dict[str, dict] = field(default_factory=dict)
    shared: dict = field(default_factory=dict)

    def section_hash(self, name: str) -> str:
        payload = {"section": self.sections.get(name, {}), "shared": self.shared}
        return stable_hash(payload)

    def hashes(self) -> dict[str, str]:
        return {name: self.section_hash(name) for name in self.sections}

    def as_prompt_json(self, name: str) -> str:
        payload = {"repo": self.target, "shared": self.shared, name: self.sections.get(name, {})}
        return json.dumps(payload, indent=2, sort_keys=True, default=str)


def stable_hash(payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
