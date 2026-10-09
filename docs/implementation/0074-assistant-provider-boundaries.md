# Slice 0074: Assistant Provider Boundaries

Status: **INTERNAL BOUNDARY IMPLEMENTED AND LOCALLY TESTED; LIVE PROVIDER
QUALIFICATION AND PRODUCT SURFACE NOT EXECUTED**. Security-critical R1 slice
under Revision 4.0 sections 4, 11, 12, and 15.

## Implemented

- Independent OpenBao-only API keys for OpenAI, Perplexity, and Gemini, with a
  bounded KV-v2 reader and explicit per-provider availability. A missing key
  does not silently select another provider.
- Fixed official API requests: OpenAI Responses `gpt-4.1-mini` with web
  search, Perplexity Agent `fast` preset, and Gemini `gemini-2.5-flash` with
  Google Search. Questions, bodies, responses, tool counts, output tokens,
  and timeout are bounded. All traffic is a model-purpose request through its
  provider-specific shared-egress profile; Gemini's key header cannot leave its
  fixed origin. Migration 0047 binds the profile to the immutable dispatch
  before I/O and refuses assistant identities on the connector-purpose binder.
- Forced-RLS, append-only provider evidence records the requested and reported
  model, response ID, exact bounded JSON, request/response digests, and observed
  egress receipt. It requires a verified site and cannot invent a successful
  response without a matching credentialed provider operation.
  The evidence operation must carry the corresponding assistant profile.
- Three separate API capability entries remain `unavailable`. This is not a
  public connector or 0075's AI-visibility baseline.

## Limits

No credentials or provider account exist in this repository. Live OpenAI,
Perplexity, and Gemini success are `NOT_EXECUTED`. Raw response evidence is not
interpreted as citations or visibility scores here. No target questions, spend
authorization, recurrence, UI, or production composition is enabled. A provider
returning incomplete, malformed, oversized, unauthorized, or unavailable data
fails explicitly; no missing observation is converted to zero. The hard-coded
models/preset require provider confirmation during live qualification before a
release can be admitted.

## Verification

```sh
.venv/bin/pytest -q tests/connectors
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/check-gsc-provider-boundary.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/pip check
npm test
git diff --cached --check
gitleaks git --log-opts="5cf0b02..HEAD" .
```

Executed counts and unexecuted live checks are in [0074 evidence](../evidence/0074-assistant-provider-boundaries.json).

## Owner Inputs for Live Qualification

Create an **OpenAI API account/project with billing and a secret API key** that
can call Responses with web search and `gpt-4.1-mini`; a **Perplexity API account
with billing and an Agent API key** that can use the `fast` preset; and a
**Google AI Studio/Gemini API project with billing and an API key** authorized
for `gemini-2.5-flash` Google Search grounding. Store each as `api_key` in
OpenBao KV-v2 paths `signal-assistants/data/openai/default`,
`signal-assistants/data/perplexity/default`, and
`signal-assistants/data/gemini/default`, respectively. Give the connector only
read access to those three paths, not mount-list or write access. Do not put keys
in shell arguments, environment variables, URLs, or repository files.

For each provider, prepare a private shared-egress context with a current
model-purpose run and robots evidence for exactly its official API origin:
`https://api.openai.com`, `https://api.perplexity.ai`, or
`https://generativelanguage.googleapis.com`. Set
`SIGNAL_ASSISTANT_EGRESS_CONTEXT` to that context JSON and
`SIGNAL_ASSISTANT_EGRESS_ADMISSION_DSN` and
`SIGNAL_ASSISTANT_EGRESS_INGEST_DSN` to the corresponding role DSNs. Run each
separately:

```sh
.venv/bin/python scripts/qualify_assistants_live.py openai
.venv/bin/python scripts/qualify_assistants_live.py perplexity
.venv/bin/python scripts/qualify_assistants_live.py gemini
```

Each command prompts silently for one API key, sends one fixed synthetic
question via shared egress, and prints provider/model readiness without answer
text or credential material. These live commands are `NOT_EXECUTED`.
