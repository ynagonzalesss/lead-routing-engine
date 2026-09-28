"""Untrusted-input handling: normalization, delimiting, and prompt-injection screening.

The original blueprint passed raw form text straight into the prompt. Anything a
prospect types is attacker-controlled input the moment this pipeline is public, so
it is normalized, bounded, wrapped in a labeled block, and screened for common
injection patterns before it ever reaches the model.
"""

from __future__ import annotations

import html
import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E]
)

_INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|earlier|"
        r"preceding|system|all|any)\b[^.\n]{0,30}\b(instructions?|prompts?|rules?|messages?|guidelines?)\b",
        re.I,
    ),
    "reveal_prompt": re.compile(
        r"\b(reveal|print|show|output|repeat|display|leak)\b[^.\n]{0,20}\b(your|the)\b[^.\n]{0,10}"
        r"\b(system\s+)?(prompt|instructions)\b",
        re.I,
    ),
    "system_prompt_mention": re.compile(r"\bsystem\s+prompt\b", re.I),
    "role_override": re.compile(
        r"\byou\s+are\s+now\b|\bact\s+as\b[^.\n]{0,20}\b(admin|system|developer)\b", re.I
    ),
    "mode_switch": re.compile(r"\b(admin|developer|debug|god|dan)\s+mode\b", re.I),
    "score_manipulation": re.compile(
        r"\b(score|rate|rank|mark|classify)\s+(this|it|me|us|my)\b(\s+lead)?\s+(as\s+|a\s+|at\s+)?"
        r"(10|ten|maximum|max|perfect|highest|priority|hot)\b"
        r"|\bicp[\s_-]*(match[\s_-]*)?score\s*(to|=|:)\s*\d+",
        re.I,
    ),
    "delimiter_tag": re.compile(
        r"</?\s*(lead_submission|field|enrichment|system|assistant|instructions?)\b", re.I
    ),
    "address_the_ai": re.compile(
        r"\b(note|message|p\.?s\.?|attention|instruction)s?\b[^.\n]{0,15}\b(to|for)\s+(the\s+)?"
        r"(ai|llm|model|assistant|bot|scorer|scoring)\b",
        re.I,
    ),
    "skip_review": re.compile(r"\bskip\s+(the\s+)?(review|checks?|validation|qualification)\b", re.I),
}


def clean_text(value: str, max_chars: int = 2000) -> str:
    """Normalize and bound untrusted text before it goes anywhere near a prompt."""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.translate(_ZERO_WIDTH)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def detect_injection(text: str) -> list[str]:
    """Return names of heuristic injection patterns found. Defense in depth, not a guarantee:
    the model-side instructions in prompts/v2_system.txt are the primary defense; this is a
    fast, auditable pre-filter that also feeds the eval set's expected-flag column.
    """
    return [name for name, pat in _INJECTION_PATTERNS.items() if pat.search(text or "")]


def escape_untrusted(text: str) -> str:
    """Escape <, > so untrusted text cannot close or forge our own delimiter tags."""
    return html.escape(text, quote=False)


def build_user_content(fields: dict[str, str]) -> str:
    """Render fields inside <lead_submission> tags. Values are escaped; keys are ours."""
    lines = ["<lead_submission>"]
    for key, value in fields.items():
        if value:
            lines.append(f'<field name="{key}">{escape_untrusted(value)}</field>')
    lines.append("</lead_submission>")
    return "\n".join(lines)
