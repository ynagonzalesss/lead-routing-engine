from lead_engine.models import Decision, Lead, Route, Segment
from lead_engine.notify import priority_alert_blocks, slack_escape


def test_slack_escape_neutralizes_channel_and_user_mentions():
    assert slack_escape("<!channel> urgent") == "&lt;!channel&gt; urgent"
    assert slack_escape("<@U12345> ping") == "&lt;@U12345&gt; ping"


def test_slack_escape_truncates():
    assert len(slack_escape("a" * 1000, limit=50)) == 50


def test_priority_alert_blocks_escapes_untrusted_pitch_and_pain_points():
    lead = Lead(lead_id="L1", email="jane@acme.io", name="Jane")
    decision = Decision(
        lead_id="L1",
        route=Route.PRIORITY,
        score=9,
        segment=Segment.MID_MARKET,
        owner="ae.midmarket.1",
        pitch="<!channel> buy now! <script>x</script>",
        signals={"company_name": "Acme", "key_pain_points": ["<b>bad</b>"], "confidence": "high"},
    )
    blocks = priority_alert_blocks(decision, lead, "https://airtable.example/rec1")
    rendered = str(blocks)
    assert "<!channel>" not in rendered
    assert "<script>" not in rendered
    assert "&lt;!channel&gt;" in rendered


def test_priority_alert_blocks_can_mask_contact():
    lead = Lead(lead_id="L1", email="jane.doe@acme.io")
    decision = Decision(lead_id="L1", route=Route.PRIORITY, score=8, signals={})
    blocks = priority_alert_blocks(decision, lead, "https://x", mask_contact=True)
    rendered = str(blocks)
    assert "jane.doe@acme.io" not in rendered
    assert "j***@acme.io" in rendered
