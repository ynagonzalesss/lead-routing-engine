# Make.com implementation blueprint

Module-by-module configuration for building this scenario in Make. This repo's
`lead_engine` package is the reference implementation and the source of truth for
the logic (regex, scoring rubric, routing rules); use it to sanity-check what you
build here, or call it directly from an HTTP/webhook module if you're hosting the
Python instead of reimplementing the logic natively in Make.

## Scenario flow

```
[ Typeform ] --(webhook)--> [1 Cleaning] -> [2 Dedup check] -> [3 Enrichment]
   -> [4 OpenAI: v2 schema] -> [5 Router]
        -> Route A (PRIORITY)  -> Airtable upsert -> Owner assignment -> Slack alert
        -> Route B (REVIEW)    -> Airtable upsert (flag=needs_review) -> Slack #ops-review
        -> Route C (NURTURE)   -> Airtable upsert (tier=self_serve) -> Email drip
        -> Route D (DISCARD)   -> Airtable log only, no notification
   -> [6 Break error handler on module 4] -> Ops Error Queue (Slack)
```

## Module 1 — Data cleaning & domain extraction (Tools > Set variable / Text parser)

Do **not** use the original blueprint's regex `(?<=@)[^.]+\.[^.]+` to pull the
domain out of the email. On a host with a subdomain and a multi-part public
suffix (`ops@london.acmegroup.co.uk`), that pattern grabs `london.acmegroup`, not
the company's real domain `acmegroup.co.uk` — see `tests/test_domain.py::
test_naive_regex_would_have_failed_on_co_uk` for the exact failure.

Make has no built-in public-suffix-list function, so pick one:

- **Recommended:** call a tiny HTTP endpoint that wraps `lead_engine.domain.
  registered_domain()` (deploy `src/lead_engine` behind a 5-line FastAPI/Flask
  wrapper, or as a Cloudflare Worker/AWS Lambda). One HTTP module, pass the
  email, get back `{"registered_domain": "...", "email_class": "..."}`.
- **Fallback (no hosting):** use Make's `parseURL` / a two-step split on `@`
  then on the last two labels of a *hardcoded* list of known multi-part
  suffixes you actually see in your traffic (`.co.uk`, `.com.au`, `.co.nz`,
  ...). Document the gap: anything outside that list falls back to "last two
  labels", which is wrong for suffixes you haven't listed. This is a real
  limitation, not a nitpick — flag it in your case study.

Also set (Set variable module):
- `email_class`: business / personal / disposable / invalid — reuse the lists
  in `src/lead_engine/domain.py` (`PERSONAL_DOMAINS`, `DISPOSABLE_DOMAINS`) as
  a Make Data Store or a big `switch()`/`contains()` filter.
- `clean_message`: strip the submission text of zero-width characters before
  building the OpenAI payload — Make's `replace()` function can strip the
  common zero-width codepoints (`​`, `‌`, `‍`, `﻿`).

## Module 2 — Dedup check (Airtable "Search Records" or Data Store "Get")

Before calling OpenAI, look up a hash of the normalized email (lowercase,
strip `+tag`, collapse gmail dots — see `lead_engine.dedup.dedup_key`) in an
Airtable table or Make Data Store keyed by that hash, with a "first seen"
timestamp. If found within your dedup window (default 30 days): route straight
to Airtable (tag `duplicate`) and stop — skip the OpenAI call entirely. This is
also your circuit breaker on accidental double-submits from a flaky form.

## Module 3 — Enrichment (HTTP module, optional)

If you fetch the lead's website for extra context:
- **Never fetch a URL the lead submitted without validating the host first.**
  Reject `localhost`, `127.0.0.1`, `169.254.169.254` (cloud metadata IP),
  anything in `10.0.0.0/8` / `172.16.0.0/12` / `192.168.0.0/16`, and any
  `.local`/`.internal`/`.corp` hostname. `lead_engine.domain.
  is_safe_public_host()` is the reference check — a lead who puts
  `http://169.254.169.254/latest/meta-data/` in the "website" field must not
  get your Make scenario's IP fetching cloud credentials on their behalf
  (SSRF). See `tests/test_domain.py::test_website_ssrf_guards_reject_
  internal_hosts`.
- Set an HTTP timeout (10s) and only take the first ~2000 characters of the
  response body forward — you're about to hand this text to an LLM prompt as
  untrusted content (see Module 4).

## Module 4 — OpenAI: Create JSON Completion (Structured Outputs)

- **Prompt:** `prompts/v2_system.txt` in this repo, not v1. v1 is kept only as
  the eval baseline that shows what the original (unhardened) prompt does on
  the same 40-lead eval set.
- **Schema:** `schemas/lead_enrichment_v2.json`, pasted as-is into the
  module's "Response Format" JSON Schema field. It only extracts signals
  (industry, size band, urgency, evidence, confidence, injection flags) — it
  does **not** output a score. Scoring happens in Module 5, deterministically.
- **User content:** build it with the `<lead_submission><field name="...">
  ...</field></lead_submission>` wrapper from `lead_engine.sanitize.
  build_user_content()` — a Make `Set variable` module doing string
  concatenation with each field HTML-escaped (`replace()` on `<` and `>`).
  This is what stops a lead's message field from closing your tags and
  forging a fake system turn (see eval leads `L019`).
- **Model:** `gpt-4o` (or your preferred Structured-Outputs-capable model).
  Temperature 0.
- **Error handler:** right-click this module → *Add error handler* →
  **Break**. Enable *Automatically complete execution*, 3 attempts, interval
  60s (Make auto-retries `RateLimitError`/`ConnectionError` already if you
  enable incomplete executions in scenario settings — the Break handler is
  for everything else). After 3 failed attempts, the incomplete execution
  should route to your Ops Error Queue Slack channel with the raw submission
  attached, per the original blueprint's design — that part was right.

## Module 5 — Router (deterministic scoring + routing)

This is the module the original blueprint skipped: instead of trusting the
LLM's own `icp_match_score`, compute the score from the extracted signals
with a fixed rubric (`lead_engine.scoring.score_signals` is the reference —
port the point table into a Make `Set variable` module with nested `if()`s,
or call the Python via HTTP the same way as Module 1):

| Signal | Points |
|---|---|
| industry ∈ {b2b_saas, fintech, digital_agency} | +3 |
| company_size_band ∈ {20-99, 100-500} | +2 |
| company_size_band = 501+ | +1 |
| pain_point_tags (1 point each, capped at 2) | +0..2 |
| urgency = high | +2 |
| urgency = medium | +1 |
| has_evaluation_timeline = true | +1 |

Then apply caps, in order: `confidence = low` → cap at 6. `email_class !=
business` → cap at 6. `is_spam_or_garbage` → force to 1. `prompt_injection_
detected` (from the model **or** your own regex pre-filter on the raw text) →
force to 1 and force route = REVIEW regardless of score.

Router filters (Make's native Router module, one branch per route):
1. **DISCARD** — `email_class` in {disposable, invalid} OR `is_spam_or_
   garbage = true`.
2. **REVIEW** — `prompt_injection_detected = true` (either detector) OR
   (`email_class = personal` AND raw_score ≥ 7) OR (`confidence = low` AND
   raw_score ≥ 7).
3. **PRIORITY** — final_score ≥ 7 (your `PRIORITY_THRESHOLD`).
4. **NURTURE** — everything else.

## Owner assignment (only for PRIORITY / REVIEW)

Airtable "Find Records" against an Owners table filtered by segment
(enterprise / mid_market / smb), then either round-robin (a Number field you
increment each assignment) or a stable hash of the lead ID (so retried runs
land on the same owner). `lead_engine.owners.OwnerAssigner` is the reference;
`config/owners.example.json` is the pool config.

## Slack notification

Use Block Kit (`lead_engine.notify.priority_alert_blocks` is the reference
JSON), not a plain-text message — and **escape** anything that came from the
LLM's `recommended_pitch` or `key_pain_points` before it goes into the Slack
payload (replace `<` → `&lt;`, `>` → `&gt;`). An LLM output field is untrusted
output the moment it reaches a channel; without escaping, a crafted pitch
string could forge `<!channel>` or `<@Uxxxxx>` mentions.

## What to screenshot for your case-study evidence

1. The Make scenario canvas (all modules + router branches).
2. Module 4's config panel showing the v2 schema pasted in.
3. Module 4's error-handler settings (Break, 3 attempts).
4. An Airtable row created from a real test submission.
5. The Slack alert it triggered.
6. The Make "Execution history" for one PRIORITY run and one injection-attempt
   run, showing it landed in REVIEW instead of PRIORITY.
