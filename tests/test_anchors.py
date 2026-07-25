"""Anchor resolution and staleness: precise where it can be, quiet when nothing meaningful moved."""
from systemmodel.core.members import member_spans
from systemmodel.core.requirements import (
    AUTHORED, NO_HASH, STALE, UNANCHORED, UNVERIFIED, VERIFIED, Requirement, apply_hashes,
    hash_for, staleness,
)

GROOVY_SERVICE = """package com.trevorism.service

import java.time.LocalDate

/**
 * Turns work history into pixel geometry.
 */
class DateToPixelTimelineService implements TimelineService {

    public static final int MAX_PIXEL_LENGTH = 1000
    public static final String GAP = "GAP"

    @Override
    Timeline generate(List<WorkHistoryItem> items) {
        double scaleFactor = MAX_PIXEL_LENGTH / total
        return new Timeline(scaleFactor)
    }

    private List<WorkHistoryItem> fillInGaps(List<WorkHistoryItem> items) {
        return items
    }
}
"""

CONTROLLER = """package com.trevorism.controller

@Controller("/api/timeline")
class TimelineController {

    private TimelineService timelineService = new DateToPixelTimelineService()

    @Secure(Roles.USER)
    @Post(value = "/")
    TimelineResponse generate(@Body List<WorkHistoryItem> items) {
        timelineService.generate(items)
    }
}
"""


def test_members_are_split_out_of_a_groovy_class():
    spans = member_spans(GROOVY_SERVICE)
    assert set(spans) == {"MAX_PIXEL_LENGTH", "GAP", "generate", "fillInGaps"}
    assert "scaleFactor" in spans["generate"]


def test_a_members_annotations_travel_with_it():
    assert "@Secure(Roles.USER)" in member_spans(CONTROLLER)["generate"]


def test_comments_and_blank_lines_are_not_part_of_a_member():
    spans = member_spans(GROOVY_SERVICE)
    assert "Turns work history" not in "".join(spans.values())


def test_a_field_is_named_for_its_declaration_not_its_initializer():
    assert "timelineService" in member_spans(CONTROLLER)
    assert "DateToPixelTimelineService" not in member_spans(CONTROLLER)


INDEX = {
    "Svc": {"body": "whole-class-digest"},
    "Svc.MAX_PIXEL_LENGTH": {"body": "constant-digest"},
    "Svc.fillInGaps": {"body": "method-digest"},
    "Other": {"body": "other-digest"},
}


def _req(*anchors: str) -> Requirement:
    return Requirement(id="R1", body="obligation", anchors=list(anchors))


def test_a_member_anchor_suppresses_the_whole_type_fallback():
    precise = _req("Svc.MAX_PIXEL_LENGTH")
    widened = dict(INDEX, Svc={"body": "class-body-changed"})
    assert hash_for(precise, INDEX) == hash_for(precise, widened)


def test_a_type_anchor_still_notices_a_change_anywhere_in_the_type():
    coarse = _req("Svc")
    widened = dict(INDEX, Svc={"body": "class-body-changed"})
    assert hash_for(coarse, INDEX) != hash_for(coarse, widened)


def test_anchors_on_other_types_survive_the_suppression():
    mixed = _req("Svc.fillInGaps", "Other")
    moved = dict(INDEX, Other={"body": "other-changed"})
    assert hash_for(mixed, INDEX) != hash_for(mixed, moved)


def test_an_anchor_that_resolves_to_nothing_has_no_hash():
    assert hash_for(_req("header X-Correlation-ID"), INDEX) == NO_HASH


def test_rebaselining_a_verified_requirement_drops_its_verdict():
    verified = Requirement(id="R1", body="obligation", anchors=["Svc.fillInGaps"],
                           origin=AUTHORED, state=VERIFIED)
    verified = apply_hashes([verified], INDEX)[0]

    moved = dict(INDEX, **{"Svc.fillInGaps": {"body": "method-changed"}})
    rebaselined = apply_hashes([verified], moved)[0]

    assert rebaselined.state == UNVERIFIED
    assert rebaselined.anchor_hash == hash_for(verified, moved)


def test_rebaselining_leaves_an_untouched_verdict_alone():
    verified = apply_hashes(
        [Requirement(id="R1", body="obligation", anchors=["Svc.fillInGaps"],
                     origin=AUTHORED, state=VERIFIED)], INDEX)[0]
    assert apply_hashes([verified], INDEX)[0].state == VERIFIED


def test_staleness_reports_moved_anchors_and_unanchored_requirements():
    tracked, untracked = _req("Svc.fillInGaps"), _req("nothing resolvable here")
    tracked, untracked = apply_hashes([tracked, untracked], INDEX)

    assert staleness([tracked, untracked], INDEX) == [(untracked, UNANCHORED)]

    moved = dict(INDEX, **{"Svc.fillInGaps": {"body": "method-changed"}})
    assert staleness([tracked], moved) == [(tracked, STALE)]
