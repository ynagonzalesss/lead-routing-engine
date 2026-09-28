"""Typed view of the v2 LLM output ("signals")."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INDUSTRIES = ("b2b_saas", "fintech", "digital_agency", "other", "unknown")
SIZE_BANDS = ("1-19", "20-99", "100-500", "501+", "unknown")
URGENCY = ("high", "medium", "low", "unknown")
PAIN_TAGS = ("manual_workflows", "scaling_sales_ops", "data_silos", "slow_lead_response")
CONFIDENCE = ("high", "medium", "low")
EVIDENCE_SIGNALS = ("industry", "company_size", "pain_point", "urgency", "timeline")


@dataclass(frozen=True)
class Evidence:
    signal: str
    quote: str


@dataclass(frozen=True)
class Signals:
    company_name: str
    industry: str
    company_size_band: str
    pain_point_tags: tuple[str, ...]
    key_pain_points: tuple[str, ...]
    urgency: str
    has_evaluation_timeline: bool
    evidence: tuple[Evidence, ...]
    confidence: str
    prompt_injection_detected: bool
    is_spam_or_garbage: bool
    recommended_pitch: str
    data_quality_notes: str
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signals:
        return cls(
            company_name=str(d["company_name"]),
            industry=d["industry"],
            company_size_band=d["company_size_band"],
            pain_point_tags=tuple(dict.fromkeys(d["pain_point_tags"])),  # de-dup, keep order
            key_pain_points=tuple(str(x) for x in d["key_pain_points"][:3]),
            urgency=d["urgency"],
            has_evaluation_timeline=bool(d["has_evaluation_timeline"]),
            evidence=tuple(Evidence(e["signal"], e["quote"]) for e in d["evidence"]),
            confidence=d["confidence"],
            prompt_injection_detected=bool(d["prompt_injection_detected"]),
            is_spam_or_garbage=bool(d["is_spam_or_garbage"]),
            recommended_pitch=str(d["recommended_pitch"]),
            data_quality_notes=str(d["data_quality_notes"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "company_name": self.company_name,
            "industry": self.industry,
            "company_size_band": self.company_size_band,
            "pain_point_tags": list(self.pain_point_tags),
            "key_pain_points": list(self.key_pain_points),
            "urgency": self.urgency,
            "has_evaluation_timeline": self.has_evaluation_timeline,
            "confidence": self.confidence,
            "prompt_injection_detected": self.prompt_injection_detected,
            "is_spam_or_garbage": self.is_spam_or_garbage,
            "recommended_pitch": self.recommended_pitch,
        }

    def with_confidence(self, confidence: str) -> Signals:
        data = {**self.__dict__, "confidence": confidence}
        return Signals(**data)
