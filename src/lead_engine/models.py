"""Core data contracts shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Route(str, Enum):
    PRIORITY = "PRIORITY"    # alert an Account Executive now
    NURTURE = "NURTURE"      # self-serve / marketing sequence, no human ping
    REVIEW = "REVIEW"        # human triage queue (ambiguous, risky, or suspicious)
    DISCARD = "DISCARD"      # log only (garbage, spam, invalid or disposable email)
    DUPLICATE = "DUPLICATE"  # already seen inside the dedup window; no new alert
    ERROR = "ERROR"          # pipeline failure; raw submission goes to the ops error queue


class Segment(str, Enum):
    ENTERPRISE = "enterprise"
    MID_MARKET = "mid_market"
    SMB = "smb"
    UNASSIGNED = "unassigned"


@dataclass(frozen=True)
class Lead:
    lead_id: str
    email: str
    name: str = ""
    company_name: str = ""
    website: str = ""
    job_title: str = ""
    message: str = ""
    enrichment_text: str = ""  # scraped landing-page text (untrusted), produced upstream


@dataclass
class Decision:
    lead_id: str
    route: Route
    score: int | None = None          # final score after caps (1-10)
    raw_score: int | None = None      # score before caps
    segment: Segment = Segment.UNASSIGNED
    owner: str | None = None
    reason: str = ""                       # one-line routing rationale
    breakdown: list[tuple[str, int]] = field(default_factory=list)
    caps_applied: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    pitch: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = ""
    stage: str = ""                   # "preflight" (no LLM call) or "llm"
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
