"""Orchestrates one lead through the v2 (hardened) pipeline end to end."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .dedup import DedupStore, dedup_key
from .domain import EmailClass, normalize_website, parse_email
from .grounding import check_grounding
from .llm import LLMClient, LLMError, LLMRefusal
from .models import Decision, Lead, Route, Segment
from .owners import OwnerAssigner
from .routing import decide_route
from .sanitize import build_user_content, clean_text, detect_injection
from .scoring import score_signals
from .signals import Signals

PROMPT_VERSION = "v2"


@dataclass
class PipelineDeps:
    llm: LLMClient
    schema: dict
    system_prompt: str
    settings: Settings
    dedup_store: DedupStore | None = None
    owner_assigner: OwnerAssigner | None = None


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def run_lead(lead: Lead, deps: PipelineDeps) -> Decision:
    """Run one lead through cleaning -> dedup -> LLM extraction -> grounding ->
    deterministic scoring -> routing -> owner assignment. Never raises: pipeline
    failures come back as a Decision with route=ERROR so the caller (a script,
    a test, or a Make "Create JSON" step) always gets a routable object.
    """
    parsed = parse_email(lead.email)
    website_domain = normalize_website(lead.website)

    if deps.dedup_store is not None:
        from datetime import timedelta

        key = dedup_key(lead.email)
        now = _now()
        if deps.dedup_store.seen_within(key, now, timedelta(days=deps.settings.dedup_window_days)):
            return Decision(lead_id=lead.lead_id, route=Route.DUPLICATE, reason="seen within dedup window")
        deps.dedup_store.record(key, now)

    if parsed.email_class in (EmailClass.INVALID, EmailClass.DISPOSABLE):
        return Decision(
            lead_id=lead.lead_id,
            route=Route.DISCARD,
            reason=f"{parsed.email_class.value} email",
            stage="preflight",
        )

    raw_message = clean_text(lead.message, deps.settings.max_input_chars)
    raw_enrichment = clean_text(lead.enrichment_text, deps.settings.max_input_chars)
    pre_injection_flags = detect_injection(raw_message) + detect_injection(raw_enrichment)

    user_content = build_user_content(
        {
            "name": lead.name,
            "company_name": lead.company_name,
            "job_title": lead.job_title,
            "email_domain": parsed.registered_domain,
            "website_domain": website_domain,
            "message": raw_message,
            "enrichment_text": raw_enrichment,
        }
    )

    try:
        result = deps.llm.extract(
            system_prompt=deps.system_prompt, user_content=user_content, schema=deps.schema
        )
    except LLMRefusal as exc:
        return Decision(lead_id=lead.lead_id, route=Route.ERROR, error=f"refusal: {exc}", stage="llm")
    except LLMError as exc:
        return Decision(lead_id=lead.lead_id, route=Route.ERROR, error=str(exc), stage="llm")

    try:
        signals = Signals.from_dict(result.data)
    except (KeyError, TypeError, ValueError) as exc:
        return Decision(
            lead_id=lead.lead_id, route=Route.ERROR, error=f"malformed LLM output: {exc}", stage="llm"
        )

    grounding_problems = check_grounding(signals, f"{raw_message}\n{raw_enrichment}")
    if grounding_problems and signals.confidence != "low":
        signals = signals.with_confidence("low")

    score = score_signals(signals, parsed.email_class)
    route, reason = decide_route(
        signals,
        score,
        parsed.email_class,
        injection_flagged=bool(pre_injection_flags),
        priority_threshold=deps.settings.priority_threshold,
    )

    owner = None
    if deps.owner_assigner is not None and route in (Route.PRIORITY, Route.REVIEW):
        owner = deps.owner_assigner.assign(score.segment, lead.lead_id)

    flags = list(pre_injection_flags)
    flags += [f"grounding:{p}" for p in grounding_problems]

    return Decision(
        lead_id=lead.lead_id,
        route=route,
        score=score.final_score,
        raw_score=score.raw_score,
        segment=score.segment if route in (Route.PRIORITY, Route.REVIEW) else Segment.UNASSIGNED,
        owner=owner,
        reason=reason,
        breakdown=score.breakdown,
        caps_applied=score.caps_applied,
        flags=flags,
        pitch=signals.recommended_pitch,
        signals=signals.as_dict(),
        prompt_version=PROMPT_VERSION,
        stage="llm",
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
