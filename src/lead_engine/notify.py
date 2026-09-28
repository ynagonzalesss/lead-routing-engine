"""Slack Block Kit rendering.

LLM output is untrusted output the moment it's rendered somewhere else: it is
escaped before it reaches a channel, so a pitch string can't forge `<!channel>`
or `<@Uxxx>` mentions.
"""

from __future__ import annotations

from typing import Any

from .domain import mask_email
from .models import Decision, Lead

_MAX_TEXT = 700


def slack_escape(text: str, limit: int = _MAX_TEXT) -> str:
    text = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text[:limit]


def priority_alert_blocks(
    decision: Decision, lead: Lead, crm_url: str, *, mask_contact: bool = False
) -> list[dict[str, Any]]:
    contact = mask_email(lead.email) if mask_contact else lead.email
    pains = "\n".join(f"• {slack_escape(p, 200)}" for p in decision.signals.get("key_pain_points", []))
    company = decision.signals.get("company_name", "Unknown")
    confidence = decision.signals.get("confidence", "?")
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "New priority lead"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Company*\n{slack_escape(str(company), 120)}"},
                {"type": "mrkdwn", "text": f"*Contact*\n{slack_escape(contact, 120)}"},
                {"type": "mrkdwn", "text": f"*Score*\n{decision.score}/10"},
                {
                    "type": "mrkdwn",
                    "text": f"*Segment / Owner*\n{decision.segment.value} / "
                    f"{slack_escape(decision.owner or 'unassigned', 60)}",
                },
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Pain points*\n{pains or '_none stated_'}"}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Suggested pitch*\n{slack_escape(decision.pitch)}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Confidence: {slack_escape(str(confidence), 20)} · <{crm_url}|Open in CRM>",
                }
            ],
        },
    ]
