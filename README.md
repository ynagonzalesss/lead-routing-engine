# Automated Intent-Based Lead Enrichment & Routing Engine

**Status:** Prototype, evaluated against a 40-lead test set. Not connected to a
live CRM or production traffic.

**Role:** AI Operations Specialist / Process Analyst
**Stack:** Typeform → Make.com → OpenAI API (Structured Outputs) → Airtable → Slack

A pipeline that takes a raw inbound lead, extracts factual signals from it with
an LLM, scores it against an ICP with a deterministic (auditable) rubric, and
routes it to the right queue — while treating everything a stranger typed into
a web form as untrusted input, because on the public internet, it is.

## The business problem

Sales teams lose hours per week manually researching new leads, deciding how
well they fit the ideal customer profile, and figuring out who should own the
account. That triage work is repetitive, inconsistent between reps, and gets
skipped entirely when volume spikes — which is exactly when it matters most.

## What changed from the first draft

The original design (kept in this repo as `prompts/v1_system.txt` +
`schemas/lead_enrichment_v1.json`, and run in the eval harness as the
baseline) asked one LLM call to both extract information *and* assign a
1–10 score directly. That has three problems in a real GTM environment:

1. **Non-reproducible scores.** The same lead can score differently between
   calls, and no one can point to *why* a lead scored 8 instead of 6.
2. **No defense against the input.** A lead's own form text goes straight
   into the prompt. Once this is public, that text is attacker-controlled.
3. **A domain-extraction regex that silently misfiles multi-part TLDs** —
   `(?<=@)[^.]+\.[^.]+` on `ops@london.acmegroup.co.uk` captures
   `london.acmegroup`, not the company's actual domain `acmegroup.co.uk`.

v2 restructures the same idea around three principles:

- **The LLM only extracts, with evidence.** It returns typed signals
  (industry, size band, urgency, pain points) each backed by a verbatim quote
  from the input. A grounding check (`lead_engine/grounding.py`) verifies every
  quote actually appears in the source text before the signal is trusted — if
  it doesn't, confidence gets downgraded rather than the pipeline shipping a
  hallucinated fact into a CRM record.
- **A fixed rubric computes the score.** Same signals in, same score out,
  every time, with a full per-factor breakdown (`lead_engine/scoring.py`).
- **Untrusted input is treated as untrusted input.** Form text is normalized,
  bounded, wrapped in labeled `<lead_submission>` tags, and screened by both a
  regex pre-filter and the model itself for prompt-injection attempts
  (`lead_engine/sanitize.py`). A lead that tries to talk the scorer into a 10
  gets routed to human review, not auto-approved.

## Architecture

```
Typeform (webhook)
  -> Clean + parse email/domain (public-suffix-aware, not a naive regex)
  -> Dedup check (normalized email hash, 30-day window)
  -> [Business email? No -> DISCARD, skip the LLM call entirely]
  -> Enrichment fetch (SSRF-guarded: rejects localhost/internal/cloud-metadata hosts)
  -> OpenAI Structured Outputs: extract signals + evidence (prompts/v2_system.txt)
  -> Grounding check: every quoted signal must appear in the source text
  -> Deterministic scoring (lead_engine/scoring.py)
  -> Routing (lead_engine/routing.py):
       DISCARD  - invalid/disposable email, spam/garbage
       REVIEW   - prompt-injection detected, OR personal email with strong signals,
                  OR low-confidence extraction with a high raw score
       PRIORITY - final score >= threshold (default 7)
       NURTURE  - everything else
  -> Owner assignment by segment (PRIORITY/REVIEW only)
  -> Airtable upsert + Slack alert (Block Kit, LLM output escaped before rendering)
```

Full module-by-module Make.com configuration: [`integrations/make/blueprint.md`](integrations/make/blueprint.md).

## System prompt & schema (v2)

- Prompt: [`prompts/v2_system.txt`](prompts/v2_system.txt)
- JSON Schema (OpenAI Structured Outputs, strict mode): [`schemas/lead_enrichment_v2.json`](schemas/lead_enrichment_v2.json)
- v1 (original, kept for comparison): [`prompts/v1_system.txt`](prompts/v1_system.txt) / [`schemas/lead_enrichment_v1.json`](schemas/lead_enrichment_v1.json)

The v2 schema deliberately has **no free-text score field**. `industry`,
`company_size_band`, and `urgency` are closed enums with an explicit
`"unknown"` option — the model is never rewarded for guessing.

## Guardrails

| Failure mode | Detection | Handling |
|---|---|---|
| Personal email (gmail/yahoo/etc.) | Domain lookup against a maintained list | Strong signals -> REVIEW (verify identity); weak signals -> NURTURE. Never auto-PRIORITY. |
| Disposable email (mailinator, etc.) | Domain lookup | DISCARD before the LLM call — saves the API cost too. |
| Garbage / spam input | Model flags `is_spam_or_garbage` | Score forced to 1, routed to DISCARD. |
| Prompt injection ("ignore previous instructions...", forged tags, "note to the AI...") | Regex pre-filter **and** model self-report; either one trips it | Score forced to 1, routed to REVIEW — a human sees it, it never silently becomes a PRIORITY lead. |
| Hallucinated / ungrounded signal | Every evidence quote checked against the source text | Confidence downgraded to `low`, capping the score at 6. |
| Multi-part domain suffix (`.co.uk`, `.com.au`, `.github.io`) | Public-suffix-aware parsing (`tldextract`, offline snapshot) | Correct registrable domain extracted; the naive single-regex approach is proven wrong in `tests/test_domain.py`. |
| SSRF via the "website" field (`localhost`, `169.254.169.254`, `.local`) | Host-safety check before any fetch | Enrichment fetch is skipped; the pipeline never calls back into a private network on a stranger's say-so. |
| Duplicate submission | Normalized-email hash (case, `+tag`, gmail dots) looked up before the LLM call | Routed to DUPLICATE, no repeat alert, no repeat API spend. |
| API timeout / rate limit | Exponential backoff with jitter, capped retries | After retries are exhausted, the pipeline returns an ERROR decision (never crashes) for the raw submission to land in an ops queue. |
| LLM output escaping | N/A | Every LLM-authored string (`recommended_pitch`, pain points) is HTML-escaped before it's rendered into a Slack message, so it can't forge `<!channel>` or `<@user>` mentions. |

## Evaluation

A 40-lead test set (`data/eval_leads.csv`) spans good fits, weak fits,
personal/disposable/invalid emails, garbage, six prompt-injection variants (one
of them a deliberate false-positive check), ambiguous/contradictory input,
multi-part-domain edge cases, and SSRF attempts. Methodology and category
breakdown: [`docs/eval-methodology.md`](docs/eval-methodology.md).

```bash
make install
make test       # 50 unit tests, offline, free, run in CI on every push
make validate   # eval-set self-consistency check, offline, free
make eval       # live run against both prompt versions - needs OPENAI_API_KEY, costs money
```

**Results:** not yet run against the live API in this environment (no network
access / API key here). Run `make eval` yourself and drop the generated
`results/eval_v1_*.md` and `results/eval_v2_*.md` in here, or link the
committed ones under `results/published/`:

| Metric | v1 (original) | v2 (hardened) |
|---|---|---|
| Route agreement vs. expected | _run `make eval` and fill in_ | _run `make eval` and fill in_ |
| Injection-handling agreement | _—_ | _—_ |
| Mean latency | _—_ | _—_ |
| Est. cost per lead | _—_ | _—_ |

I'm not publishing invented numbers here — the eval harness produces real
ones the moment you run it with a key, and that's the artifact worth trusting.

## Limitations & what I'd do next

- **Not load-tested.** The Make scenario and the reference Python are both
  single-lead-at-a-time designs; a real burst (e.g. a webinar signup spike)
  would need queueing and rate-limit-aware backoff tuned against actual
  traffic, not the synthetic eval set.
- **Public-suffix list is a point-in-time snapshot.** `tldextract` ships an
  offline copy so the pipeline never makes a network call to parse a domain,
  but that means it needs periodic updates as new TLDs appear.
- **The injection regex pre-filter is defense-in-depth, not a guarantee.**
  It catches known patterns; the model's own `prompt_injection_detected` flag
  and the REVIEW-not-auto-approve routing rule are the real backstop.
- **No feedback loop yet.** The natural next step is a Slack 👍/👎 on each
  PRIORITY alert that writes back to Airtable, plus a weekly rollup of
  AE-confirmed vs. rejected leads — that closes the loop the eval set alone
  can't: whether the rubric's weights actually predict closed deals.
- **At 10x volume** I'd move the scoring rubric out of Make's native modules
  and into the hosted Python service directly (one HTTP call instead of a
  dozen Make modules), and add a real queue (SQS/Cloud Tasks) between the
  webhook and the OpenAI call so a burst doesn't hit rate limits scenario-wide.

## Repository layout

```
src/lead_engine/     Reference implementation (domain parsing, sanitization,
                     signals, grounding, scoring, routing, dedup, owners,
                     Slack rendering, LLM client, pipeline orchestration, eval CLI)
prompts/             v1 (original) and v2 (hardened) system prompts
schemas/             Matching OpenAI Structured Outputs JSON schemas
data/eval_leads.csv  40-lead evaluation set with expected labels
tests/               50 offline unit + pipeline tests (fake LLM client, no network)
docs/                Eval methodology
integrations/make/   Module-by-module Make.com blueprint
config/              Example owner-pool configuration
results/             Eval reports land here when you run `make eval`
```

## License

MIT — see [LICENSE](LICENSE).
