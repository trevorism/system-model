"""Reviewed exposures leave the review list but stay auditable; new ones surface alone."""
from systemmodel.core.platform import render_platform

CENSUS = {"service": ["event", "network"]}
EXPOSURE = [
    ("event", {"public_mutating": ["POST /event/{topic}"]}),
    ("network", {"public_mutating": ["POST /api/node/{id}"]}),
]
ACK = {"event": {"POST /event/{topic}": "ingress is deliberately open"}}


def _platform_body(exposure, acknowledged):
    nodes = render_platform({}, CENSUS, ["service"], ["event", "network"], {"test"},
                            exposure=exposure, acknowledged=acknowledged)
    return next(n.body for n in nodes if n.id == "platform")


def test_unreviewed_route_is_listed_for_review():
    body = _platform_body(EXPOSURE, ACK)
    review = body.split("## Unauthenticated writes")[1].split("### Reviewed and accepted")[0]
    assert "POST /api/node/{id}" in review
    assert "POST /event/{topic}" not in review


def test_reviewed_route_stays_auditable_with_its_reason():
    body = _platform_body(EXPOSURE, ACK)
    accepted = body.split("### Reviewed and accepted")[1]
    assert "POST /event/{topic}" in accepted
    assert "ingress is deliberately open" in accepted


def test_all_reviewed_reports_none_unreviewed():
    everything = {"event": {"POST /event/{topic}": "open by design"},
                  "network": {"POST /api/node/{id}": "open by design"}}
    body = _platform_body(EXPOSURE, everything)
    review = body.split("## Unauthenticated writes")[1].split("### Reviewed and accepted")[0]
    assert "None unreviewed" in review


def test_a_new_exposure_appears_alone_after_a_full_review():
    """The point of the mechanism: the next one added is not buried in the accepted list."""
    everything = {"event": {"POST /event/{topic}": "open by design"},
                  "network": {"POST /api/node/{id}": "open by design"}}
    grown = EXPOSURE + [("memo", {"public_mutating": ["POST /api/danger"]})]
    body = _platform_body(grown, everything)
    review = body.split("## Unauthenticated writes")[1].split("### Reviewed and accepted")[0]
    assert "POST /api/danger" in review
    assert "None unreviewed" not in review
    assert review.count("- **") == 1


def test_acknowledging_one_route_does_not_excuse_another_in_the_same_repo():
    exposure = [("homepage", {"public_mutating": ["POST /api/user", "POST /api/danger"]})]
    body = _platform_body(exposure, {"homepage": {"POST /api/user": "registration"}})
    review = body.split("## Unauthenticated writes")[1].split("### Reviewed and accepted")[0]
    assert "POST /api/danger" in review
    assert "POST /api/user" not in review


def test_non_service_repos_are_scanned_and_labelled():
    """Scoping the security list to services hid whole repo kinds from it."""
    exposure = [("endpoint-tester", {"public_mutating": ["POST /api/json"], "kind": "tester"}),
                ("event", {"public_mutating": ["POST /event/{topic}"], "kind": "service"})]
    review = _platform_body(exposure, None).split("## Unauthenticated writes")[1]
    assert "**endpoint-tester** _(tester)_" in review
    assert "**event**: " in review
    assert "_(service)_" not in review


def test_no_acknowledgements_behaves_as_before():
    body = _platform_body(EXPOSURE, None)
    review = body.split("## Unauthenticated writes")[1]
    assert "POST /event/{topic}" in review
    assert "POST /api/node/{id}" in review
    assert "Reviewed and accepted" not in body
