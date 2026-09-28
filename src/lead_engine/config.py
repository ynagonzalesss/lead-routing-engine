"""Runtime configuration, loaded from environment variables (12-factor style)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _opt_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


@dataclass(frozen=True)
class Settings:
    openai_model: str = "gpt-4o"
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 3
    priority_threshold: int = 7
    dedup_window_days: int = 30
    max_input_chars: int = 2000
    cost_per_1m_input_usd: float | None = None
    cost_per_1m_output_usd: float | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            openai_model=os.environ.get("OPENAI_MODEL", cls.openai_model),
            llm_timeout_s=float(os.environ.get("LLM_TIMEOUT_S", cls.llm_timeout_s)),
            llm_max_retries=int(os.environ.get("LLM_MAX_RETRIES", cls.llm_max_retries)),
            priority_threshold=int(os.environ.get("PRIORITY_THRESHOLD", cls.priority_threshold)),
            dedup_window_days=int(os.environ.get("DEDUP_WINDOW_DAYS", cls.dedup_window_days)),
            max_input_chars=int(os.environ.get("MAX_INPUT_CHARS", cls.max_input_chars)),
            cost_per_1m_input_usd=_opt_float("COST_PER_1M_INPUT_USD"),
            cost_per_1m_output_usd=_opt_float("COST_PER_1M_OUTPUT_USD"),
        )
