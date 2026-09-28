"""Loading versioned prompts and JSON schemas from the repo root."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ("v1", "v2")


@cache
def load_prompt(version: str) -> str:
    if version not in VERSIONS:
        raise ValueError(f"unknown version {version!r}")
    return (ROOT / "prompts" / f"{version}_system.txt").read_text(encoding="utf-8").strip()


@cache
def _load_schema_raw(version: str) -> str:
    if version not in VERSIONS:
        raise ValueError(f"unknown version {version!r}")
    return (ROOT / "schemas" / f"lead_enrichment_{version}.json").read_text(encoding="utf-8")


def load_schema(version: str) -> dict[str, Any]:
    """Return the full OpenAI response_format json_schema object (name, strict, schema)."""
    return json.loads(_load_schema_raw(version))
