"""Hallucination guard: every non-trivial extracted signal must cite a quote that is
actually present in the source text. This is what makes the LLM's output auditable
rather than a black box — see docs/eval-methodology.md.
"""

from __future__ import annotations

import html
import re

from .signals import Signals

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NON_ALNUM.sub(" ", html.unescape(text).lower()).strip()


def check_grounding(signals: Signals, source_text: str) -> list[str]:
    """Return a list of grounding problems (empty list means fully grounded)."""
    problems: list[str] = []
    haystack = f" {_norm(source_text)} "

    for ev in signals.evidence:
        needle = _norm(ev.quote)
        if not needle or f" {needle} " not in haystack:
            problems.append(f"quote_not_in_input:{ev.signal}")

    have = {e.signal for e in signals.evidence}
    required: list[str] = []
    if signals.industry != "unknown":
        required.append("industry")
    if signals.company_size_band != "unknown":
        required.append("company_size")
    if signals.pain_point_tags:
        required.append("pain_point")
    if signals.urgency in ("high", "medium"):
        required.append("urgency")
    if signals.has_evaluation_timeline:
        required.append("timeline")
    problems.extend(f"missing_evidence:{sig}" for sig in required if sig not in have)
    return problems
