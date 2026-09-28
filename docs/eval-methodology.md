# Eval methodology

## Why an eval set instead of a demo

A demo shows the happy path once. Hiring managers (and production incidents)
care about the other 90% of traffic: personal emails, garbage, duplicates, and
someone actively trying to break the scoring. `data/eval_leads.csv` has 40
leads across 13 categories, each with an expected email classification,
expected injection flag, expected route, and (where meaningful) an expected
score range.

| Category | Count | What it tests |
|---|---|---|
| good_fit | 5 | Clean ICP matches across b2b_saas/fintech/agency |
| weak_fit | 4 | Wrong industry, wrong size, or no signal — must NOT over-score |
| personal_email | 3 | Gmail/Yahoo — routes to REVIEW or NURTURE, never straight to PRIORITY |
| disposable_email | 2 | Mailinator/Guerrilla Mail — DISCARD before wasting an API call |
| invalid_email | 3 | Malformed, double-@, fake TLD |
| garbage | 2 | Keyboard mashing, vendor spam — forces score=1, DISCARD |
| injection / injection_negative | 6 | Direct override, role-play, tag-forging, and a false-positive check |
| ambiguous | 2 | Missing size/industry — confidence must be low, capping the score |
| edge_domain | 3 | `.co.uk`, `.com.au`, `.github.io` — multi-part public suffixes |
| minimal_input | 2 | Near-empty submissions |
| unicode_noise | 1 | Zero-width chars, curly quotes — must not break quote-matching |
| long_input | 1 | Exceeds `MAX_INPUT_CHARS`, must truncate not crash |
| contradictory | 1 | Self-contradictory size claim |
| ssrf_website | 2 | Loopback / `.local` website field — must not be treated as a real domain |
| duplicate_variant | 1 | Same identity, different casing — dedup key must match (see unit tests) |

## Two layers of "does this work"

1. **Unit tests** (`tests/`, `make test`) are deterministic and offline: they
   pin down the scoring rubric, routing rules, dedup key normalization,
   domain parsing, and injection-pattern matching against a fake LLM client.
   These run in CI on every push and cost nothing.
2. **The eval harness** (`lead_engine.eval`, `make eval`) calls the *real*
   OpenAI API against every row in `data/eval_leads.csv` and reports:
   - **Route agreement** — did the full pipeline's final route match the
     expected route?
   - **Score-in-range** — did the final score fall inside the row's expected
     band (where one is given)?
   - **Injection-handling agreement** — did the pipeline correctly flag (or
     correctly not flag) each row for injection?
   - **Latency** (mean, p95) and **token usage** (in/out), and estimated cost
     if you set `COST_PER_1M_INPUT_USD` / `COST_PER_1M_OUTPUT_USD` in `.env`.
   - A **mismatches table** naming every lead where the pipeline disagreed
     with the label, so you can inspect exactly which ones and why.

`make validate` is the offline half of the eval harness: it checks the CSV
itself is well-formed (every `expected_route`/`expected_email_class` is a real
enum value, ids are unique, score ranges are sane) and cross-checks each row's
`expected_email_class` against what `lead_engine.domain.parse_email()` actually
computes for that address — so a mislabeled row in the eval set itself gets
caught before you ever spend an API call on it.

## Running it

```bash
make install
make test        # unit tests, offline, free
make validate    # eval-set sanity check, offline, free
cp .env.example .env   # fill in OPENAI_API_KEY
make eval        # live API calls against both v1 and v2 - costs money
```

Reports land in `results/eval_<version>_<timestamp>.{json,md}`. Commit the
ones you want to show as evidence into `results/published/` (the rest of
`results/` is gitignored so you don't accidentally commit every local run).

## What "prompt v1 → v2" is actually testing

v1 is the *original* draft prompt and schema — the LLM outputs `icp_match_score`
directly, with no grounding, no injection defense, and no deterministic rubric.
v2 only extracts factual signals with cited evidence; `lead_engine.scoring`
turns those into a score. Running both through the same 40 leads and diffing
the reports is the evidence that v2 is a real improvement, not just a rewrite:
watch specifically for v1's route-agreement rate collapsing on the `injection`
and `personal_email` categories, and v2's grounding check catching evidence
that doesn't quote the source text.
