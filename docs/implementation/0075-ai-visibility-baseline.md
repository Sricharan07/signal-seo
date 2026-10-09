# Slice 0075: AI-Visibility Baseline

Status: **IMPLEMENTED LOCALLY; LIVE PROVIDER QUALIFICATION NOT EXECUTED; R1
PARTIAL**. Product-tier slice under Revision 4.0 section 15. It consumes only
the qualified 0074 provider boundary and creates no authority, credential, or
egress capability.

## Implemented

- A completed crawl manifest supplies bounded, deterministic target-question
  candidates from immutable page title or heading evidence. The owner list is
  separately bounded. A new question set supersedes the preceding set without
  mutating either version; every crawl-derived target points to its exact page
  evidence.
- Citation parsers recognize only each provider's documented structured citation
  fields: OpenAI Responses URL annotations, Perplexity citation URLs, and Gemini
  grounding chunks. Answer prose, including instruction-like text, is data and
  cannot create a citation. Malformed or excessive citation structures yield an
  explicit incomplete observation, never an inferred zero.
- Immutable forced-RLS observations bind a target question to provider, model,
  observation time, provider evidence identity when a response completed,
  cited site pages, other cited domains, bounded usage, and a reserved cost
  amount. Unconfigured, rate-limited, unavailable, malformed, or cap-limited
  provider attempts retain explicit incomplete coverage and empty citation
  fields.
- The dashboard adds a per-site **AI visibility** view. It is evidence-first,
  has no invented score, states incomplete coverage plainly, and tells owners
  that official API results can differ from consumer applications.

## Limits

The fixed provider models and their OpenBao-held credentials remain the 0074
boundary. No live provider credential was supplied, so OpenAI, Perplexity, and
Gemini calls are **NOT_EXECUTED**. This slice does not enable a scheduler,
recurrence, owner write control, production composition, external write, or an
AI-visibility optimization agent. A cost reservation bounds every potential
call; actual provider token usage is recorded only where the provider reports
it and is not represented as a fabricated price.

## Qualification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling tests/connectors
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/pip check
npm test
gitleaks git --log-opts="4e24605..HEAD" .
```

Exact local outcomes are in [the evidence record](../evidence/0075-ai-visibility-baseline.json).
