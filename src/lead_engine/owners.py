"""Account-owner assignment by segment.

The original blueprint's "Router Module" stopped at posting a Slack alert — it
never actually assigned an owner, despite the project being named a *routing*
engine. This closes that gap.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .models import Segment


class OwnerAssigner:
    """strategy='stable_hash': idempotent (a retried lead lands on the same owner),
    roughly even distribution, no shared counter needed.
    strategy='round_robin': perfectly even, but requires shared counter state
    (e.g. an Airtable "last assigned index" record) to be safe under concurrent runs.
    """

    def __init__(self, pools: dict[str, list[str]], strategy: str = "stable_hash") -> None:
        if strategy not in ("stable_hash", "round_robin"):
            raise ValueError(f"unknown strategy: {strategy}")
        if not pools.get(Segment.UNASSIGNED.value):
            raise ValueError("pools must include a non-empty 'unassigned' fallback pool")
        self.pools = pools
        self.strategy = strategy
        self._counters: dict[str, int] = defaultdict(int)

    @classmethod
    def from_file(cls, path: str | Path) -> OwnerAssigner:
        cfg = json.loads(Path(path).read_text())
        return cls(cfg["pools"], cfg.get("strategy", "stable_hash"))

    def assign(self, segment: Segment, key: str) -> str:
        pool = self.pools.get(segment.value) or self.pools[Segment.UNASSIGNED.value]
        if self.strategy == "round_robin":
            idx = self._counters[segment.value] % len(pool)
            self._counters[segment.value] += 1
        else:
            idx = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(pool)
        return pool[idx]
