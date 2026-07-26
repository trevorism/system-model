"""Requirement records: parsing both formats, round-tripping, and preserving authored intent."""
from systemmodel.core.requirements import (
    AUTHORED, DERIVED, NO_HASH, UNVERIFIED, VERIFIED, Requirement, hashes, hydrate, merge, parse,
    reconcile, render,
)

LEGACY = """R1. Anyone on the internet may publish to any existing topic; only topic and subscription administration is gated, so the ingest path stays open to its eight consuming repos.
    -> EventController.sendEvent (no @Secure), SubscriptionController/TopicController @Secure(Roles.USER), event.data.trevorism.com

R2. The caller's bearer token and tenant are copied verbatim onto message attributes so push subscribers can authenticate; this service never validates or strips them itself.
    -> PubSubEventService.addToken, SecureHttpClient.AUTHORIZATION, ServerAuthentication attribute "tenant"
"""

WRAPPED_LEGACY = """R1. Ownership is decided from the signed token, never from cookies, because the identity
    cookies this service sets are readable and writable by the browser.
    -> ImageController.isOwnerOrAdmin, Authentication
"""


def test_legacy_prose_parses_into_records():
    reqs = parse(LEGACY)
    assert [r.id for r in reqs] == ["R1", "R2"]
    assert reqs[0].body.startswith("Anyone on the internet may publish")
    assert reqs[0].origin == DERIVED
    assert reqs[0].state == UNVERIFIED
    assert reqs[0].anchor_hash == NO_HASH


def test_anchor_line_is_split_but_not_inside_parens_or_quotes():
    reqs = parse(LEGACY)
    assert reqs[0].anchors == [
        "EventController.sendEvent (no @Secure)",
        "SubscriptionController/TopicController @Secure(Roles.USER)",
        "event.data.trevorism.com",
    ]
    assert reqs[1].anchors[-1] == 'ServerAuthentication attribute "tenant"'


def test_a_wrapped_body_collapses_to_one_paragraph():
    reqs = parse(WRAPPED_LEGACY)
    assert "\n" not in reqs[0].body
    assert reqs[0].body.endswith("readable and writable by the browser.")
    assert reqs[0].anchors == ["ImageController.isOwnerOrAdmin", "Authentication"]


def test_prose_with_no_requirements_yields_nothing():
    assert parse("Just some paragraph that never numbers anything.") == []


def test_records_round_trip_through_render_and_parse():
    """The prose carries everything except the anchor hash, which the manifest restores."""
    original = [
        Requirement(id="R1", body="First obligation.", anchors=["AController.go"],
                    origin=AUTHORED, anchor_hash="4a1c2f09", state=VERIFIED,
                    finding="Checked the handler."),
        Requirement(id="R2", body="Second obligation.", anchors=["BService"]),
    ]
    reparsed = hydrate(parse(render(original)), hashes(original))
    assert reparsed == original


def test_a_record_at_its_defaults_carries_no_annotation():
    """Almost every record is derived and unverified; only deviation is worth showing."""
    plain = render([Requirement(id="R1", body="Just a description.", anchors=["X"])])
    assert plain.splitlines()[0] == "### R1"

    promoted = render([Requirement(id="R1", body="Binding.", origin=AUTHORED, state=VERIFIED)])
    assert promoted.splitlines()[0] == "### R1 — authored, verified"


def test_render_orders_numerically_not_lexically():
    reqs = [Requirement(id=f"R{n}", body=f"Body {n}.") for n in (10, 2, 1)]
    assert [r.id for r in parse(render(reqs))] == ["R1", "R2", "R10"]


def test_parse_prefers_blocks_when_both_shapes_could_match():
    text = render([Requirement(id="R7", body="Block body.", anchors=["X"])])
    reqs = parse(text)
    assert [r.id for r in reqs] == ["R7"]
    assert reqs[0].body == "Block body."


def test_parse_falls_back_to_legacy_prose():
    assert [r.id for r in parse(LEGACY)] == ["R1", "R2"]


def test_symbols_pulls_identifiers_and_ignores_prose():
    req = Requirement(id="R1", body="x", anchors=[
        "PubSubEventService.addCorrelationId", "header X-Correlation-ID",
        "consumers: action, auth-provider",
    ])
    symbols = req.symbols()
    assert "PubSubEventService.addCorrelationId" in symbols
    assert "action" not in symbols
    assert "header" not in symbols


def test_authored_records_survive_a_resynthesis_that_replaces_everything():
    prior = [
        Requirement(id="R1", body="Derived and disposable."),
        Requirement(id="R2", body="Binding intent.", origin=AUTHORED, state=VERIFIED),
    ]
    fresh = [Requirement(id="R1", body="Newly synthesized.")]
    merged = merge(prior, fresh)

    authored = [r for r in merged if r.is_authored]
    assert len(authored) == 1
    assert authored[0].id == "R2"
    assert authored[0].body == "Binding intent."
    assert authored[0].state == VERIFIED
    assert "Derived and disposable." not in [r.body for r in merged]


def test_fresh_records_are_renumbered_around_the_authored_ones():
    prior = [Requirement(id="R2", body="Held.", origin=AUTHORED)]
    fresh = [Requirement(id="R1", body="a"), Requirement(id="R2", body="b"),
             Requirement(id="R3", body="c")]
    merged = merge(prior, fresh)
    assert [(r.id, r.body) for r in merged] == [
        ("R1", "a"), ("R2", "Held."), ("R3", "b"), ("R4", "c"),
    ]


def test_an_empty_synthesis_keeps_what_was_there():
    prior = [Requirement(id="R1", body="Derived."),
             Requirement(id="R2", body="Authored.", origin=AUTHORED)]
    assert merge(prior, []) == prior


def test_merge_marks_every_fresh_record_derived_even_if_it_claims_otherwise():
    fresh = [Requirement(id="R1", body="Synthesis cannot promote itself.", origin=AUTHORED)]
    assert merge([], fresh)[0].origin == DERIVED


def test_legacy_prose_still_parses_so_an_old_model_can_migrate():
    """Nothing writes this shape any more; reading it is what makes migration free."""
    assert [r.id for r in parse(LEGACY)] == ["R1", "R2"]


def test_rendered_records_round_trip_through_the_new_format():
    once = render(parse(LEGACY))
    assert "<!--" not in once
    assert render(parse(once)) == once


def test_reconcile_keeps_authored_intent_when_synthesis_replaces_the_rest():
    prior = [Requirement(id="R1", body="Disposable."),
             Requirement(id="R2", body="Binding.", origin=AUTHORED, state=VERIFIED)]
    result = reconcile(prior, parse(LEGACY))
    held = [r for r in result if r.is_authored]
    assert [(r.id, r.body, r.state) for r in held] == [("R2", "Binding.", VERIFIED)]
    assert "Disposable." not in [r.body for r in result]
    assert len(result) == 3  # the held one plus both freshly synthesized


def test_a_fresh_record_restating_authored_intent_is_dropped_not_cloned():
    """Re-running a no-op derive must not add a derived copy of every promotion."""
    promoted = Requirement(id="R2", body="Binding intent.", origin=AUTHORED, state=VERIFIED)
    prior = [Requirement(id="R1", body="Description."), promoted]

    merged = merge(prior, list(prior))  # the shape the decompose keep-path used to pass

    assert [r.body for r in merged].count("Binding intent.") == 1
    assert len([r for r in merged if r.is_authored]) == 1
