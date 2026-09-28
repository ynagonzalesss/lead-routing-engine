"""Evaluation harness.

`validate`: offline. Checks data/eval_leads.csv is well-formed and internally
consistent (every expected_route/email_class/score is a real enum value, score
ranges make sense, ids are unique). Runs in CI on every push; costs nothing.

`run`: calls the live OpenAI API against every row in the eval set, computes
per-row agreement with the expected labels, and writes a JSON + Markdown report
to results/. Costs money and needs OPENAI_API_KEY — never run in CI.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from .assets import load_prompt, load_schema
from .config import Settings
from .dedup import InMemoryDedupStore
from .domain import EmailClass, parse_email
from .llm import LLMError, LLMRefusal, OpenAIClient
from .models import Lead, Route
from .owners import OwnerAssigner
from .pipeline import PipelineDeps, run_lead
from .sanitize import detect_injection

ROOT = Path(__file__).resolve().parents[2]
EVAL_CSV = ROOT / "data" / "eval_leads.csv"
RESULTS_DIR = ROOT / "results"

VALID_ROUTES = {r.value for r in Route}
VALID_EMAIL_CLASSES = {c.value for c in EmailClass}


def load_eval_rows(path: Path = EVAL_CSV) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate_eval_set(path: Path = EVAL_CSV) -> list[str]:
    """Return a list of problems; empty means the eval set is well-formed."""
    problems: list[str] = []
    if not path.exists():
        return [f"eval file not found: {path}"]
    rows = load_eval_rows(path)
    if not rows:
        return ["eval file has no rows"]

    seen_ids: set[str] = set()
    required_cols = {
        "lead_id", "email", "name", "company_name", "website", "job_title", "message",
        "enrichment_text", "category", "expected_email_class", "expected_injection",
        "expected_route", "expected_score_min", "expected_score_max", "notes",
    }
    missing_cols = required_cols - set(rows[0].keys())
    if missing_cols:
        problems.append(f"missing columns: {sorted(missing_cols)}")
        return problems

    for i, row in enumerate(rows, start=2):  # header is line 1
        lid = row["lead_id"].strip()
        if not lid:
            problems.append(f"line {i}: empty lead_id")
        elif lid in seen_ids:
            problems.append(f"line {i}: duplicate lead_id {lid!r}")
        seen_ids.add(lid)

        route = row["expected_route"].strip()
        if route not in VALID_ROUTES:
            problems.append(f"line {i} ({lid}): invalid expected_route {route!r}")

        eclass = row["expected_email_class"].strip()
        if eclass not in VALID_EMAIL_CLASSES:
            problems.append(f"line {i} ({lid}): invalid expected_email_class {eclass!r}")

        inj = row["expected_injection"].strip().lower()
        if inj not in ("true", "false"):
            problems.append(f"line {i} ({lid}): expected_injection must be true/false, got {inj!r}")

        lo, hi = row["expected_score_min"].strip(), row["expected_score_max"].strip()
        if lo or hi:
            try:
                lo_i, hi_i = int(lo), int(hi)
                if not (1 <= lo_i <= hi_i <= 10):
                    problems.append(f"line {i} ({lid}): score range {lo}-{hi} out of bounds")
            except ValueError:
                problems.append(f"line {i} ({lid}): non-integer score range {lo!r}/{hi!r}")

        # Cross-checks: does the actual email address match the label the row claims?
        actual_class = parse_email(row["email"]).email_class.value
        if eclass and actual_class != eclass:
            problems.append(
                f"line {i} ({lid}): expected_email_class={eclass!r} but email parses as {actual_class!r}"
            )
        detected = bool(detect_injection(row["message"]) or detect_injection(row["enrichment_text"]))
        if inj == "true" and not detected and row["category"].strip() == "injection":
            # informational only for categories other than 'injection'; here it's a real gap
            problems.append(
                f"line {i} ({lid}): labeled expected_injection=true but the regex pre-filter misses it"
            )

    return problems


def _row_to_lead(row: dict[str, str]) -> Lead:
    return Lead(
        lead_id=row["lead_id"],
        email=row["email"],
        name=row.get("name", ""),
        company_name=row.get("company_name", ""),
        website=row.get("website", ""),
        job_title=row.get("job_title", ""),
        message=row.get("message", ""),
        enrichment_text=row.get("enrichment_text", ""),
    )


def _score_in_range(score: int | None, lo: str, hi: str) -> bool | None:
    if not lo or not hi or score is None:
        return None
    return int(lo) <= score <= int(hi)


def run_v2(rows: list[dict[str, str]], settings: Settings) -> dict:
    llm = OpenAIClient(
        model=settings.openai_model,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    deps = PipelineDeps(
        llm=llm,
        schema=load_schema("v2"),
        system_prompt=load_prompt("v2"),
        settings=settings,
        dedup_store=InMemoryDedupStore(),
        owner_assigner=OwnerAssigner.from_file(ROOT / "config" / "owners.example.json"),
    )
    results = []
    for row in rows:
        lead = _row_to_lead(row)
        decision = run_lead(lead, deps)
        route_match = decision.route.value == row["expected_route"].strip()
        score_ok = _score_in_range(decision.score, row["expected_score_min"], row["expected_score_max"])
        injection_expected = row["expected_injection"].strip().lower() == "true"
        injection_caught = bool(decision.flags and any(
            f.startswith(tuple(["ignore_", "reveal_", "system_", "role_", "mode_", "score_", "delimiter_",
                                 "address_", "skip_"])) for f in decision.flags
        )) or (decision.signals.get("prompt_injection_detected") is True)
        results.append(
            {
                "lead_id": lead.lead_id,
                "category": row["category"],
                "route": decision.route.value,
                "expected_route": row["expected_route"],
                "route_match": route_match,
                "score": decision.score,
                "score_in_expected_range": score_ok,
                "injection_expected": injection_expected,
                "injection_caught": injection_caught,
                "injection_match": injection_caught == injection_expected,
                "latency_ms": decision.latency_ms,
                "input_tokens": decision.input_tokens,
                "output_tokens": decision.output_tokens,
                "error": decision.error,
            }
        )
    return _summarize("v2", results, settings)


def run_v1(rows: list[dict[str, str]], settings: Settings) -> dict:
    """v1 has no grounding, no deterministic scoring, and no injection defense - it
    is run through the raw prompt/schema only, so the report shows what the
    *original* design actually does on the same eval set.
    """
    llm = OpenAIClient(
        model=settings.openai_model,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    schema = load_schema("v1")
    system_prompt = load_prompt("v1")
    tier_to_route = {
        "Enterprise Tier": "PRIORITY",
        "Mid-Market": "PRIORITY",
        "Self-Serve / Low Priority": "NURTURE",
        "Review Required": "REVIEW",
    }
    results = []
    for row in rows:
        user_content = (
            f"Email: {row['email']}\nName: {row.get('name', '')}\n"
            f"Company: {row.get('company_name', '')}\nWebsite: {row.get('website', '')}\n"
            f"Job title: {row.get('job_title', '')}\nMessage: {row.get('message', '')}\n"
            f"Enrichment text: {row.get('enrichment_text', '')}"
        )
        try:
            result = llm.extract(system_prompt=system_prompt, user_content=user_content, schema=schema)
            data = result.data
            route = tier_to_route.get(data.get("assigned_tier"), "REVIEW")
            score = data.get("icp_match_score")
            error = None
            latency, itok, otok = result.latency_ms, result.input_tokens, result.output_tokens
        except (LLMError, LLMRefusal) as exc:
            route, score, error, latency, itok, otok = "ERROR", None, str(exc), None, None, None

        injection_expected = row["expected_injection"].strip().lower() == "true"
        results.append(
            {
                "lead_id": row["lead_id"],
                "category": row["category"],
                "route": route,
                "expected_route": row["expected_route"],
                "route_match": route == row["expected_route"].strip(),
                "score": score,
                "score_in_expected_range": _score_in_range(
                    score, row["expected_score_min"], row["expected_score_max"]
                ),
                "injection_expected": injection_expected,
                "injection_caught": False,  # v1 has no injection defense at all
                "injection_match": (not injection_expected),
                "latency_ms": latency,
                "input_tokens": itok,
                "output_tokens": otok,
                "error": error,
            }
        )
    return _summarize("v1", results, settings)


def _summarize(version: str, results: list[dict], settings: Settings) -> dict:
    n = len(results)
    route_agree = sum(r["route_match"] for r in results)
    score_checked = [r for r in results if r["score_in_expected_range"] is not None]
    score_agree = sum(1 for r in score_checked if r["score_in_expected_range"])
    inj_agree = sum(r["injection_match"] for r in results)
    errors = sum(1 for r in results if r["error"])
    latencies = [r["latency_ms"] for r in results if r["latency_ms"] is not None]
    in_tok = sum(r["input_tokens"] or 0 for r in results)
    out_tok = sum(r["output_tokens"] or 0 for r in results)

    cost_usd = None
    if settings.cost_per_1m_input_usd is not None and settings.cost_per_1m_output_usd is not None:
        cost_usd = (in_tok / 1_000_000) * settings.cost_per_1m_input_usd + (
            out_tok / 1_000_000
        ) * settings.cost_per_1m_output_usd

    return {
        "version": version,
        "model": settings.openai_model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_leads": n,
        "route_agreement_rate": route_agree / n if n else None,
        "score_in_range_rate": (score_agree / len(score_checked)) if score_checked else None,
        "injection_handling_agreement_rate": inj_agree / n if n else None,
        "error_rate": errors / n if n else None,
        "latency_ms_mean": statistics.mean(latencies) if latencies else None,
        "latency_ms_p95": (statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 5 else None),
        "total_input_tokens": in_tok,
        "total_output_tokens": out_tok,
        "estimated_cost_usd": cost_usd,
        "results": results,
    }


def _write_reports(summary: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = RESULTS_DIR / f"eval_{summary['version']}_{ts}.json"
    json_path.write_text(json.dumps(summary, indent=2))

    md_path = RESULTS_DIR / f"eval_{summary['version']}_{ts}.md"
    lines = [
        f"# Eval report — prompt {summary['version']} — {summary['model']}",
        "",
        f"Generated: {summary['generated_at']}",
        f"Leads evaluated: {summary['n_leads']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Route agreement | {_pct(summary['route_agreement_rate'])} |",
        f"| Score-in-expected-range | {_pct(summary['score_in_range_rate'])} |",
        f"| Injection-handling agreement | {_pct(summary['injection_handling_agreement_rate'])} |",
        f"| Error rate | {_pct(summary['error_rate'])} |",
        f"| Mean latency | {_ms(summary['latency_ms_mean'])} |",
        f"| P95 latency | {_ms(summary['latency_ms_p95'])} |",
        f"| Total tokens (in/out) | {summary['total_input_tokens']} / {summary['total_output_tokens']} |",
        f"| Estimated cost | {_cost(summary['estimated_cost_usd'])} |",
        "",
        "## Mismatches",
        "",
        "| lead_id | category | expected_route | actual_route | score | error |",
        "|---|---|---|---|---|---|",
    ]
    for r in summary["results"]:
        if not r["route_match"] or r.get("error"):
            lines.append(
                f"| {r['lead_id']} | {r['category']} | {r['expected_route']} | {r['route']} | "
                f"{r['score']} | {r.get('error') or ''} |"
            )
    md_path.write_text("\n".join(lines) + "\n")
    return json_path


def _pct(v: float | None) -> str:
    return f"{v*100:.1f}%" if v is not None else "n/a"


def _ms(v: float | None) -> str:
    return f"{v:.0f} ms" if v is not None else "n/a"


def _cost(v: float | None) -> str:
    return f"${v:.4f}" if v is not None else "not configured (set COST_PER_1M_*_USD in .env)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lead_engine.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    run_p = sub.add_parser("run")
    run_p.add_argument("--version", choices=["v1", "v2", "both"], default="v2")

    args = parser.parse_args(argv)

    if args.command == "validate":
        problems = validate_eval_set()
        if problems:
            print(f"FAIL: {len(problems)} problem(s) in {EVAL_CSV}:")
            for p in problems:
                print(f"  - {p}")
            return 1
        rows = load_eval_rows()
        print(f"OK: {EVAL_CSV} is valid ({len(rows)} leads).")
        return 0

    settings = Settings.from_env()
    rows = load_eval_rows()
    versions = ["v1", "v2"] if args.version == "both" else [args.version]
    for v in versions:
        try:
            summary = run_v1(rows, settings) if v == "v1" else run_v2(rows, settings)
        except LLMError as exc:
            print(f"FAIL: could not run the {v} eval: {exc}")
            print("Set OPENAI_API_KEY (copy .env.example to .env and fill it in) and try again.")
            return 1
        path = _write_reports(summary)
        print(f"{v}: route agreement {_pct(summary['route_agreement_rate'])}, report at {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
