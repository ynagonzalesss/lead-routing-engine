from lead_engine.grounding import check_grounding
from lead_engine.signals import Evidence, Signals


def _signals(**overrides):
    base = dict(
        company_name="Acme",
        industry="b2b_saas",
        company_size_band="100-500",
        pain_point_tags=("manual_workflows",),
        key_pain_points=("reps copy data manually",),
        urgency="high",
        has_evaluation_timeline=True,
        evidence=(
            Evidence("industry", "B2B SaaS company"),
            Evidence("company_size", "120-person"),
            Evidence("pain_point", "manually copying data into the CRM"),
            Evidence("urgency", "before our Q1 sales kickoff"),
            Evidence("timeline", "before our Q1 sales kickoff"),
        ),
        confidence="high",
        prompt_injection_detected=False,
        is_spam_or_garbage=False,
        recommended_pitch="pitch",
        data_quality_notes="None",
    )
    base.update(overrides)
    return Signals(**base)


SOURCE = (
    "We're a 120-person B2B SaaS company. Our reps spend 10+ hours a week manually "
    "copying data into the CRM and we're losing deals to slow follow-up. We want to "
    "fix this before our Q1 sales kickoff."
)


def test_fully_grounded_signals_have_no_problems():
    assert check_grounding(_signals(), SOURCE) == []


def test_fabricated_quote_is_flagged():
    s = _signals(evidence=(Evidence("industry", "a completely made up sentence not in the source"),))
    problems = check_grounding(s, SOURCE)
    assert any(p.startswith("quote_not_in_input") for p in problems)


def test_missing_evidence_for_a_non_unknown_field_is_flagged():
    s = _signals(evidence=())  # claims industry/size/etc but cites nothing
    problems = check_grounding(s, SOURCE)
    assert any("missing_evidence:industry" in p for p in problems)
    assert any("missing_evidence:company_size" in p for p in problems)


def test_unknown_fields_do_not_require_evidence():
    s = _signals(
        industry="unknown",
        company_size_band="unknown",
        pain_point_tags=(),
        urgency="unknown",
        has_evaluation_timeline=False,
        evidence=(),
    )
    assert check_grounding(s, "some unrelated text") == []
