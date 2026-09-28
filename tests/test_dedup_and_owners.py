from datetime import datetime, timedelta, timezone

import pytest

from lead_engine.dedup import InMemoryDedupStore, dedup_key
from lead_engine.models import Segment
from lead_engine.owners import OwnerAssigner


def test_dedup_key_normalizes_case_and_gmail_dots_and_plus_tag():
    assert dedup_key("Jane.Doe@Gmail.com") == dedup_key("janedoe@gmail.com")
    assert dedup_key("jane.doe+newsletter@gmail.com") == dedup_key("janedoe@gmail.com")
    assert dedup_key("jane@acme.io") != dedup_key("jane@acme.com")


def test_dedup_store_window():
    store = InMemoryDedupStore()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    key = dedup_key("jane@acme.io")
    assert store.seen_within(key, now, timedelta(days=30)) is False
    store.record(key, now)
    assert store.seen_within(key, now + timedelta(days=1), timedelta(days=30)) is True
    assert store.seen_within(key, now + timedelta(days=31), timedelta(days=30)) is False


def test_owner_assigner_stable_hash_is_idempotent():
    pools = {"enterprise": ["a", "b"], "mid_market": ["c", "d"], "smb": ["e"], "unassigned": ["f"]}
    oa = OwnerAssigner(pools, strategy="stable_hash")
    first = oa.assign(Segment.MID_MARKET, "lead-42")
    second = oa.assign(Segment.MID_MARKET, "lead-42")
    assert first == second
    assert first in pools["mid_market"]


def test_owner_assigner_round_robin_cycles():
    pools = {"enterprise": ["a", "b"], "mid_market": ["c"], "smb": ["d"], "unassigned": ["e"]}
    oa = OwnerAssigner(pools, strategy="round_robin")
    seq = [oa.assign(Segment.ENTERPRISE, f"lead-{i}") for i in range(4)]
    assert seq == ["a", "b", "a", "b"]


def test_owner_assigner_falls_back_to_unassigned_pool():
    pools = {"unassigned": ["u1"]}
    oa = OwnerAssigner(pools)
    assert oa.assign(Segment.ENTERPRISE, "lead-1") == "u1"


def test_owner_assigner_requires_unassigned_pool():
    with pytest.raises(ValueError):
        OwnerAssigner({"enterprise": ["a"]})
