"""Deterministic routing policy. Order matters: earlier rules win."""

from __future__ import annotations

from .domain import EmailClass
from .models import Route
from .scoring import ScoreResult
from .signals import Signals


def decide_route(
    signals: Signals,
    score: ScoreResult,
    email_class: EmailClass,
    *,
    injection_flagged: bool,
    priority_threshold: int,
) -> tuple[Route, str]:
    """Return (route, reason). `injection_flagged` is the pre-filter's independent
    verdict (sanitize.detect_injection); `signals.prompt_injection_detected` is the
    model's own verdict. Either one triggers REVIEW.
    """
    if email_class in (EmailClass.INVALID, EmailClass.DISPOSABLE):
        return Route.DISCARD, f"{email_class.value} email"
    if signals.is_spam_or_garbage:
        return Route.DISCARD, "spam or garbage input"
    if injection_flagged or signals.prompt_injection_detected:
        return Route.REVIEW, "possible prompt injection; human review required"
    if email_class is EmailClass.PERSONAL:
        if score.raw_score >= priority_threshold:
            return Route.REVIEW, "strong signals but personal email; verify identity"
        return Route.NURTURE, "personal email, below priority threshold"
    if signals.confidence == "low" and score.raw_score >= priority_threshold:
        return Route.REVIEW, "high raw score but low confidence; verify"
    if score.final_score >= priority_threshold:
        return Route.PRIORITY, f"score {score.final_score} >= {priority_threshold}"
    return Route.NURTURE, f"score {score.final_score} < {priority_threshold}"
