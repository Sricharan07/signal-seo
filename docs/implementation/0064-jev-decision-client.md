# Slice 0064: Jev Decision Client

Status: **IMPLEMENTED AND LOCALLY QUALIFIED; LIVE TYPESAFE SUCCESS NOT EXECUTED**.

## Objective

Add the Revision 4.0 Jev decision boundary without giving any model authority.
Signal can submit bounded typed questions, validate the complete response, record an
immutable recommendation, and fall back conservatively when Jev is unavailable.

## Implemented

- Added validated Choice, Noul, and Score contracts. Choice accepts 2–255 options,
  Score accepts 2–10 levels, and state, instructions, and criteria accept only text
  or JSON objects and arrays. Canonical request bytes are capped at 64 KiB, the
  longest question is capped at 32 KiB, and provider-reported input usage above
  65,536 tokens is rejected.
- Added a thin fixed-origin provider adapter for
  `POST https://api.typesafe.ai/v1/systemone`. It requests `jev-latest`, requires a
  versioned response model, and bounds time and response bytes. Slice 0065 removed
  its direct public HTTP path; it now requires the shared-egress capability, which
  owns redirect denial, public-address screening, peer pinning, and TLS.
- Added strict response validation. Exact answer ids and types are required;
  Choice and Score probability maps must have the submitted keys, finite normalized
  values, and a sum of one; Choice must select a highest-probability submitted
  option; Score must echo the submitted legend and weighted score; confidence and
  Noul values must remain in `[0,1]`. Invalid evidence is unavailable, never a
  partial answer.
- Added a single-version, read-only OpenBao Jev credential capability at the exact
  `signal-decision/data/typesafe/default` path. The credential is held only in
  memory and is absent from errors, records, and representations.
- Added a labelled Luna structured-classification fallback for unavailable,
  rate-limited, rejected, timed-out, invalid, or unconfigured Jev. It can return
  only `ask_owner` or `reject`. If that provider is also unavailable, deterministic
  rules return `ask_owner` or `reject`; no fallback can recommend `ship`.
- Added immutable forced-RLS PostgreSQL decision records through one function-only
  workflow role. Each record stores input and question-schema digests, exact
  answers, recommendation probabilities, confidence, threshold, policy ceiling,
  final outcome, provider/model provenance, and an explicit fallback flag/reason.
- Added a recommendation safety lattice, `reject < ask_owner < ship`. Every result
  is reduced to the deterministic policy ceiling before it is returned or stored.
  The service accepts no authorizer, executor, connector, dispatcher, merger, or
  deployer capability, proving the local INV-026 boundary.
- Added a one-shot live qualification command that reads the TypeSafe API key
  silently and through standard input. It uses only synthetic state and writes only
  sanitized output. Live evidence remains `NOT_EXECUTED` because no key exists.

## Provider Contract And Assumptions

The adapter follows TypeSafe's official
[HTTP API reference](https://docs.typesafe.ai/api),
[model limits](https://docs.typesafe.ai/models), and
[Choice](https://docs.typesafe.ai/primitives/choice),
[Score](https://docs.typesafe.ai/primitives/score), and
[Noul](https://docs.typesafe.ai/primitives/noul) contracts as read on 2026-09-26.
ADR-0061 isolates three undocumented details: a `0.000001` probability-sum
tolerance, exact Score-legend echo for structured criteria, and conservative local
byte preflight because TypeSafe does not publish its tokenizer contract. Any live
mismatch fails closed and requires a reviewed adapter revision.

## Verification

```sh
PYTHONPATH=services/control_plane/src .venv/bin/pytest -q \
  tests/tooling/test_jev_decisions.py \
  tests/identity/test_model_credentials.py \
  tests/tooling/test_openbao_lab.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/ruff check apps services scripts tests
.venv/bin/ruff format --check apps services scripts tests
.venv/bin/pip check
npm test
npm audit --omit=dev
```

The focused suite covers positive typed results, request limits, unknown choices,
malformed/non-normalized probabilities, out-of-range confidence, wrong models,
timeouts, transport failure, 401, 429, 5xx, 529, and unconfigured operation. It
also enumerates every recommendation/ceiling pair and verifies no result can widen
the deterministic ceiling.

The disposable PostgreSQL 17.11 lab covers exact replay, conflicting identity,
scope mismatch, authority reduction, immutable update/delete rejection, forced
RLS, function-only access, fallback evidence, and migration rollback. The
disposable TLS OpenBao 2.6.1 lab proves a single Jev secret can be read while its
credential token cannot mutate it.

Recorded local results are in
[the slice evidence](../evidence/0064-jev-decision-client.json): 582 PostgreSQL
cases, 844 API/identity/tooling cases, nine real OpenBao checks, 24 repository
cases, and 106 dashboard cases passed. The separate
[live qualification evidence](../evidence/0064-jev-live-qualification.json) remains
`NOT_EXECUTED`.

Run the live qualification only after the owner has a TypeSafe key:

```sh
scripts/qualify-jev-provider.sh
```

Slice 0065 additionally requires `SIGNAL_JEV_EGRESS_CONTEXT`,
`SIGNAL_JEV_EGRESS_ADMISSION_DSN`, and `SIGNAL_JEV_EGRESS_INGEST_DSN`. The context
is a secret-free exact running authority for only `https://api.typesafe.ai`; the
DSNs use the function-only shared-egress roles. The command fails before provider
I/O when that boundary is absent or invalid.

The command does not update evidence automatically. Review its sanitized result,
then replace the separate `NOT_EXECUTED` evidence in a dedicated qualification
slice. Never paste the key into a shell argument, environment variable, log, test,
or repository file.

## Limits And Next Work

No authorized TypeSafe call has succeeded, so Jev is not provider-qualified. This
slice exposes no API or dashboard route and is not composed into the browser,
Business Brain, workflow scheduler, standing authorization, or autonomy gate. It
does not authorize or execute work and creates no repository, provider, merge,
deployment, delete, or production-write authority.

Slice 0065 closes the direct-egress limit: Jev and the labelled fallback now have no
public HTTP client and require injected shared egress. Live TypeSafe behavior is
still unqualified, and product/workflow composition remains absent.
