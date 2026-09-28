"""Duplicate-submission detection with a pluggable store.

In Make, the equivalent lookup is an Airtable "search records" or Data Store "get"
module keyed on this same hash, run before the OpenAI module so duplicates never
cost an API call. See integrations/make/blueprint.md, module 2b.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Protocol

_GMAIL = {"gmail.com", "googlemail.com"}


def dedup_key(email: str) -> str:
    """Stable hash of a normalized email (lowercase, +tag stripped, gmail dots removed)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return hashlib.sha256(email.encode()).hexdigest()
    local, host = email.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if host in _GMAIL:
        local = local.replace(".", "")
        host = "gmail.com"
    return hashlib.sha256(f"{local}@{host}".encode()).hexdigest()


class DedupStore(Protocol):
    def seen_within(self, key: str, now: datetime, window: timedelta) -> bool: ...
    def record(self, key: str, now: datetime) -> None: ...


class InMemoryDedupStore:
    """Reference implementation for tests and local runs."""

    def __init__(self) -> None:
        self._seen: dict[str, datetime] = {}

    def seen_within(self, key: str, now: datetime, window: timedelta) -> bool:
        ts = self._seen.get(key)
        return ts is not None and now - ts <= window

    def record(self, key: str, now: datetime) -> None:
        self._seen[key] = now
