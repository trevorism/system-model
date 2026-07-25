from pathlib import Path

import pytest

from systemmodel.core import config
from systemmodel.core.platform import (
    SignalSpec, aggregate, conformance, exception_lines, render_platform,
)

SECURITY = SignalSpec(key="security.enabled", label="Micronaut security enabled",
                      kind="invariant", type="bool")
RUNTIME = SignalSpec(key="test.runtime", label="Test runtime", kind="convention", type="value")
SPECS = {SECURITY.key: SECURITY, RUNTIME.key: RUNTIME}
AUTHORED = {"security.enabled": True, "test.runtime": "junit5"}
REASON = "pure computation, nothing to protect"

RECORDS = [
    ("auth", {"security.enabled": True, "test.runtime": "junit5"}),
    ("event", {"security.enabled": True, "test.runtime": "junit5"}),
    ("timeline", {"security.enabled": False, "test.runtime": "junit5"}),
]
EXCEPTIONS = {"security.enabled": {"timeline": REASON}}


def _aggs(records=None, exceptions=EXCEPTIONS):
    return aggregate(records or RECORDS, SPECS, AUTHORED, exceptions)


def test_excepted_repo_is_not_a_violator():
    conf = conformance(_aggs())
    assert conf.violating_signals == []
    assert conf.repos_in_violation == []


def test_exception_is_reported_not_swallowed():
    agg = _aggs()["security.enabled"]
    assert agg.excepted() == [("timeline", False, REASON)]
    assert len(agg.violators()) == 0
    assert exception_lines(conformance(_aggs())) == [
        f"Micronaut security enabled: timeline=`no` — {REASON}"
    ]


def test_requirement_still_bites_for_another_repo():
    records = RECORDS + [("draw", {"security.enabled": False, "test.runtime": "junit5"})]
    conf = conformance(_aggs(records))
    assert conf.repos_in_violation == ["draw"]
    assert [a.spec.key for a in conf.violating_signals] == ["security.enabled"]


def test_exception_is_scoped_to_its_own_signal():
    records = [("timeline", {"security.enabled": False, "test.runtime": "spock"})]
    conf = conformance(_aggs(records))
    assert conf.repos_in_violation == ["timeline"]
    assert [a.spec.key for a in conf.violating_signals] == ["test.runtime"]


def _rendered(name: str, exceptions=EXCEPTIONS) -> str:
    census = {"service": [r for r, _ in RECORDS]}
    nodes = render_platform(_aggs(exceptions=exceptions), census, ["service"],
                            [r for r, _ in RECORDS], {"micronaut-groovy"})
    return next(n.body for n in nodes if n.path == name)


def test_exception_is_visible_in_rendered_model():
    invariants = _rendered("invariants.md")
    assert "2/3 conform" in invariants
    assert "excepted by `platform.toml` (1): timeline=`no`" in invariants
    assert REASON in invariants

    platform = _rendered("platform.md")
    assert "### Authored exceptions" in platform
    assert "authored exception(s) below" in platform
    assert REASON in platform


def test_no_exceptions_leaves_output_unchanged():
    invariants = _rendered("invariants.md", exceptions={})
    assert "excepted by `platform.toml`" not in invariants
    assert "2/3 conform  ⚠ violations: timeline" in invariants
    assert "Authored exceptions" not in _rendered("platform.md", exceptions={})


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SYSTEMMODEL_DIR", str(tmp_path))
    config._load.cache_clear()
    yield tmp_path
    config._load.cache_clear()


def test_exceptions_parsed_from_platform_toml(model_dir: Path):
    (model_dir / "platform.toml").write_text(
        '[invariants]\n'
        'security.enabled = true\n'
        '\n'
        '[[exceptions]]\n'
        'signal = "security.enabled"\n'
        'repo   = "timeline"\n'
        f'reason = "{REASON}"\n',
        encoding="utf-8")
    assert config.authored_exceptions() == {"security.enabled": {"timeline": REASON}}
    assert config.authored_signals() == {"security.enabled": True}


def test_malformed_exception_entries_are_skipped(model_dir: Path):
    (model_dir / "platform.toml").write_text(
        '[[exceptions]]\n'
        'signal = "security.enabled"\n'
        '\n'
        '[[exceptions]]\n'
        'repo = "timeline"\n'
        '\n'
        '[[exceptions]]\n'
        'signal = "coverage.gate"\n'
        'repo = "draw"\n',
        encoding="utf-8")
    assert config.authored_exceptions() == {"coverage.gate": {"draw": ""}}
