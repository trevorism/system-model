"""Render-path integration: authored prose survives re-derivation and never counts as drift."""
from pathlib import Path

from systemmodel.core.overlay import PLACEHOLDER
from systemmodel.core.render import render
from systemmodel.core.schema import Level, Node

BODY = f"""# Capabilities

#### As anyone, I can submit an event. <!-- cap:event.send -->

<!-- intent:event.send -->
{PLACEHOLDER}
<!-- /intent -->

#### As an admin, I can remove a topic. <!-- cap:topic.delete -->

<!-- intent:topic.delete -->
{PLACEHOLDER}
<!-- /intent -->
"""


def _node(body: str = BODY) -> Node:
    return Node(Level.L1, "capabilities", "capabilities", "capabilities.md",
                body=body, supports_authored=True)


def _render(root: Path, node: Node):
    return render(root, [node], adapter="test", target="demo", generated_at="t")


def _hash(result) -> str:
    return next(n["content_hash"] for n in result.manifest["nodes"] if n["id"] == "capabilities")


def test_prose_preserved_and_hash_stable(tmp_path: Path):
    node = _node()
    first = _render(tmp_path, node)

    # A human fills in intent prose in the written file.
    doc = tmp_path / "capabilities.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            f"<!-- intent:event.send -->\n{PLACEHOLDER}\n<!-- /intent -->",
            "<!-- intent:event.send -->\n> intent: the platform event spine.\n<!-- /intent -->",
        ),
        encoding="utf-8",
    )

    # Re-deriving the SAME code must preserve the prose and leave the content hash unchanged.
    second = _render(tmp_path, node)
    assert "> intent: the platform event spine." in doc.read_text(encoding="utf-8")
    assert _hash(first) == _hash(second)  # prose edit is not drift
    assert second.dropped_authored == []


def test_dropped_authored_reported_when_capability_removed(tmp_path: Path):
    _render(tmp_path, _node())
    doc = tmp_path / "capabilities.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            f"<!-- intent:topic.delete -->\n{PLACEHOLDER}\n<!-- /intent -->",
            "<!-- intent:topic.delete -->\n> intent: soon to vanish.\n<!-- /intent -->",
        ),
        encoding="utf-8",
    )

    # New derivation no longer produces the topic.delete capability.
    shrunk = BODY[: BODY.index("#### As an admin")]
    result = _render(tmp_path, _node(shrunk))
    assert result.dropped_authored == ["capabilities.md:topic.delete"]
