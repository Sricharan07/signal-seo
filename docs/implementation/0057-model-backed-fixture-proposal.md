# Slice 0057: Model-Backed Fixture Proposal

## Objective

Replace the visible fixture proposal's deterministic copy step with one real,
bounded `gpt-5.6-luna` Responses call while preserving durable intent, exact
evidence, deterministic safety policy, human approval, and zero external writes.

## Implemented

- Added an OpenAI Responses adapter fixed to `gpt-5.6-luna`, one hashed metadata
  instruction release, canonical synthetic input, strict JSON Schema output, no
  tools, `store=false`, low reasoning effort, bounded timeout/output, response
  validation, token accounting, and sanitized failures.
- Added a narrow OpenBao credential reader and disposable-pilot provisioning. The
  pilot removes `SIGNAL_OPENAI_API_KEY` from its environment before starting the
  API/dashboard and gives the adapter only exact read access to one separately
  mounted secret.
- Added forward-only database revision `0032` with forced-RLS, guarded
  `app.agent_runs` and `app.model_calls`. A model intent commits before provider
  I/O; known failures permit at most three attempts and unknown outcomes prohibit
  blind retry.
- Extended proposal revisions with a discriminated producer and model-call
  reference. PostgreSQL reconstructs the complete accepted model manifest from
  current evidence and durable provider facts before sealing the RFC 8785 bytes,
  SHA-256 digest, approval request, and one-cent pilot cost ceiling.
- Added the authenticated model-fixture API operation and composed it through the
  same-origin Signal Chat command. Approvals renders model release, requested and
  reported model, response identity, prompt/input/output hashes, token usage,
  rationale, exact copy, deterministic checks, authority, and recovery.
- Preserved the deterministic Slice 0056 revisions as readable historical records.

## Safety Properties

- The browser supplies no prompt, evidence, model, credential, copy, tools,
  authority, or external target. The selected site and current owner authority are
  resolved from the exact server session before and after model I/O.
- The model receives only a fixed synthetic metadata packet and cannot invoke
  tools. It cannot read the configured customer origin, approve work, issue a
  command, access GitHub, or mutate any external system.
- Provider intent is durable before I/O. Sanitized known failures are retryable
  only within the attempt ceiling; transport or persistence ambiguity is recorded
  as `unknown` and blocks automatic repetition.
- The credential is not stored in PostgreSQL, model output, logs, source control,
  browser state, or evidence JSON. OpenBao and its secret are invocation-owned and
  destroyed with the pilot.
- Approval still accepts only the exact local draft. It writes no outbox event and
  grants no merge, deploy, provider, customer-origin, or production authority.

## Verification

Run from the repository root:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/tooling/test_model_reasoning.py tests/identity/test_model_credentials.py tests/api/test_proposals_http.py tests/api/test_api.py -q
.venv/bin/python -m pytest tests/tooling/test_openbao_lab.py tests/tooling/test_model_reasoning.py tests/identity/test_model_credentials.py -q
.venv/bin/python scripts/openbao_lab.py
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm test
.venv/bin/ruff check apps services scripts tests
.venv/bin/ruff format --check apps services scripts tests
npm audit --omit=dev
.venv/bin/pip check
```

The live proof uses a privately injected developer credential with `npm run pilot`,
then completes sign-in, site creation, synthetic Work, fixture analysis, Luna
proposal preparation, and Approval inspection. The committed record contains the
provider response identity, model-reported value, token usage, and three content
hashes, but no credential or provider response body.

Evidence is recorded in
[0057-model-backed-fixture-proposal.json](../evidence/0057-model-backed-fixture-proposal.json).

## Limits And Next Work

This is one model-backed synthetic content responsibility, not the nine-role
workforce. The exact provider model name is recorded but is not treated as an
immutable production snapshot. There is no arbitrary chat, customer-page read,
GSC import, competitor research, repository checkout, patch/build, Telegram,
GitHub operation, live verification, production deployment, or external undo.

Next, replace fixture evidence with one authorized immutable customer-page
observation. Only then should the bounded role handoff feed a credential-free
repository candidate builder; GitHub remains disabled until its independent
binding, build, review, exact approval, reconciliation, and recovery gates pass.
