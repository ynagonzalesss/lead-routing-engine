from lead_engine.domain import EmailClass
from lead_engine.models import Segment
from lead_engine.routing import decide_route
from lead_engine.scoring import score_signals
from lead_engine.signals import Signals


def _signals(**overrides):
    base = dict(
        company_name="Acme",
        industry="b2b_saas",
        company_size_band="100-500",
        pain_point_tags=("manual_workflows", "slow_lead_response"),
        key_pain_points=("a", "b"),
        urgency="high",
        has_evaluation_timeline=True,
        evidence=(),
        confidence="high",
        prompt_injection_detected=False,
        is_spam_or_garbage=False,
        recommended_pitch="pitch",
        data_quality_notes="None",
    )
    base.update(overrides)
    return Signals(**base)


def test_strong_signals_score_high_and_route_priority():
    s = _signals()
    score = score_signals(s, EmailClass.BUSINESS)
    assert score.final_score >= 8
    assert score.segment is Segment.MID_MARKET
    route, _ = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=False, priority_threshold=7)
    assert route.value == "PRIORITY"


def test_weak_signals_score_low_and_route_nurture():
    s = _signals(industry="other", company_size_band="1-19", urgency="low",
                 has_evaluation_timeline=False, pain_point_tags=())
    score = score_signals(s, EmailClass.BUSINESS)
    assert score.final_score <= 2
    route, _ = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=False, priority_threshold=7)
    assert route.value == "NURTURE"


def test_low_confidence_caps_final_score_but_high_raw_score_still_flags_for_review():
    # Strong signals (raw_score would clear the threshold) but low confidence:
    # the final (capped) score is held below the threshold, and because the
    # *raw* score was high, routing sends it to REVIEW rather than silently
    # dropping it into NURTURE — a human should double check a lead that looks
    # this strong but was extracted with low confidence.
    s = _signals(confidence="low")
    score = score_signals(s, EmailClass.BUSINESS)
    assert score.final_score <= 6  # LOW_CONFIDENCE_CAP
    assert score.raw_score >= 7
    route, reason = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=False, priority_threshold=7)
    assert route.value == "REVIEW"
    assert "confidence" in reason


def test_low_confidence_with_genuinely_weak_signals_routes_nurture():
    s = _signals(
        confidence="low", industry="other", company_size_band="unknown",
        urgency="low", has_evaluation_timeline=False, pain_point_tags=(),
    )
    score = score_signals(s, EmailClass.BUSINESS)
    assert score.raw_score < 7
    route, _ = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=False, priority_threshold=7)
    assert route.value == "NURTURE"


def test_personal_email_with_strong_signals_goes_to_review_not_priority():
    s = _signals()
    score = score_signals(s, EmailClass.PERSONAL)
    route, reason = decide_route(s, score, EmailClass.PERSONAL, injection_flagged=False, priority_threshold=7)
    assert route.value == "REVIEW"
    assert "personal" in reason


def test_personal_email_with_weak_signals_goes_to_nurture():
    s = _signals(industry="other", company_size_band="1-19", urgency="low", has_evaluation_timeline=False)
    score = score_signals(s, EmailClass.PERSONAL)
    route, _ = decide_route(s, score, EmailClass.PERSONAL, injection_flagged=False, priority_threshold=7)
    assert route.value == "NURTURE"


def test_spam_forces_score_to_1_and_discard():
    s = _signals(is_spam_or_garbage=True)
    score = score_signals(s, EmailClass.BUSINESS)
    assert score.final_score == 1
    route, _ = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=False, priority_threshold=7)
    assert route.value == "DISCARD"


def test_prompt_injection_forces_review_even_with_high_raw_score():
    s = _signals(prompt_injection_detected=True)
    score = score_signals(s, EmailClass.BUSINESS)
    assert score.final_score == 1
    route, reason = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=False, priority_threshold=7)
    assert route.value == "REVIEW"


def test_preflight_injection_flag_alone_forces_review():
    s = _signals(prompt_injection_detected=False)  # model missed it
    score = score_signals(s, EmailClass.BUSINESS)
    route, _ = decide_route(s, score, EmailClass.BUSINESS, injection_flagged=True, priority_threshold=7)
    assert route.value == "REVIEW"  # our regex pre-filter still catches it


def test_disposable_and_invalid_email_always_discard_regardless_of_signals():
    s = _signals()
    score = score_signals(s, EmailClass.DISPOSABLE)
    route, _ = decide_route(s, score, EmailClass.DISPOSABLE, injection_flagged=False, priority_threshold=7)
    assert route.value == "DISCARD"
    route2, _ = decide_route(s, score, EmailClass.INVALID, injection_flagged=False, priority_threshold=7)
    assert route2.value == "DISCARD"


def test_score_breakdown_is_fully_auditable():
    s = _signals()
    score = score_signals(s, EmailClass.BUSINESS)
    assert len(score.breakdown) == 5
    assert sum(v for _, v in score.breakdown) == score.raw_score
