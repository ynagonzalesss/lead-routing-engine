"""Deterministic ICP scoring.

The original blueprint asked the LLM to output `icp_match_score` directly, which
means the same lead can score differently on different calls, and no one can point
to *why* a lead scored 8 instead of 6. Here the LLM only extracts factual signals
(see signals.py); a fixed, testable rubric turns those signals into a score. Same
signals in, same score out, every time, with a full audit trail (`breakdown`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .domain import EmailClass
from .models import Segment
from .signals import Signals

RUBRIC_VERSION = "2.0"

INDUSTRY_POINTS = {"b2b_saas": 3, "fintech": 3, "digital_agency": 3, "other": 0, "unknown": 1}
SIZE_POINTS = {"20-99": 2, "100-500": 2, "501+": 1, "1-19": 0, "unknown": 1}
URGENCY_POINTS = {"high": 2, "medium": 1, "low": 0, "unknown": 0}
TIMELINE_POINTS = 1
PAIN_POINTS_MAX = 2          # 1 point per distinct pain tag, capped
LOW_CONFIDENCE_CAP = 6       # below the default priority threshold of 7
NON_BUSINESS_EMAIL_CAP = 6

SEGMENT_BY_SIZE = {
    "501+": Segment.ENTERPRISE,
    "100-500": Segment.MID_MARKET,
    "20-99": Segment.SMB,
}


@dataclass
class ScoreResult:
    raw_score: int
    final_score: int
    breakdown: list[tuple[str, int]] = field(default_factory=list)
    caps_applied: list[str] = field(default_factory=list)
    segment: Segment = Segment.UNASSIGNED


def _clamp(n: int) -> int:
    return max(1, min(10, n))


def score_signals(signals: Signals, email_class: EmailClass) -> ScoreResult:
    parts: list[tuple[str, int]] = [
        (f"industry={signals.industry}", INDUSTRY_POINTS[signals.industry]),
        (f"size={signals.company_size_band}", SIZE_POINTS[signals.company_size_band]),
        (
            f"pain_points={len(signals.pain_point_tags)}",
            min(len(signals.pain_point_tags), PAIN_POINTS_MAX),
        ),
        (f"urgency={signals.urgency}", URGENCY_POINTS[signals.urgency]),
        (
            f"evaluation_timeline={signals.has_evaluation_timeline}",
            TIMELINE_POINTS if signals.has_evaluation_timeline else 0,
        ),
    ]
    raw = _clamp(sum(p for _, p in parts))
    final = raw
    caps: list[str] = []

    if signals.confidence == "low" and final > LOW_CONFIDENCE_CAP:
        final = LOW_CONFIDENCE_CAP
        caps.append("low_confidence")
    if email_class is not EmailClass.BUSINESS and final > NON_BUSINESS_EMAIL_CAP:
        final = NON_BUSINESS_EMAIL_CAP
        caps.append(f"{email_class.value}_email")
    if signals.is_spam_or_garbage:
        final = 1
        caps.append("spam_or_garbage")
    if signals.prompt_injection_detected:
        final = 1
        caps.append("prompt_injection")

    return ScoreResult(
        raw_score=raw,
        final_score=final,
        breakdown=parts,
        caps_applied=caps,
        segment=SEGMENT_BY_SIZE.get(signals.company_size_band, Segment.UNASSIGNED),
    )
