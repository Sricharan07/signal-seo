# ADR-0142: Role Models, Writing Quality and Monthly Budget

Status: Accepted for the internal boundary; live qualification NOT_EXECUTED.
Date: 2026-10-03.
Classification: Security-critical.

## Decision

Use `gpt-6-luna` for the existing OpenAI reasoner roles. Efforts are low for
page type and report text; medium for fact extraction, metadata and claim
extraction; high for article outline, draft, critique and revision. Editorial
entailment fallback is explicitly labelled and always high effort. An operator
may configure a role's model, effort and integer price snapshot through process
configuration, never through a brief, voice excerpt, page or model output.
An alternate model requires an explicit conservative price snapshot; it is not
an evaluated production release merely because configuration accepts its name.

Every call retains closed schemas, no tools, `store=false`, bounded request and
response sizes, and the existing shared-egress profile. A stable system style
prefix precedes untrusted task data; a versioned `prompt_cache_key` supports
automatic prompt caching. Do not claim a cache hit without reported cached
tokens, or opt into extended retention. Record actual requested/reported model,
effort, configuration release, input/output identities and usage.

The owner-selected price snapshot is USD 0.10 input, 0.01 cached input and 0.50
output per million tokens. The documented cache-write ceiling is USD 0.125.
Reserve conservatively using UTF-8 request bytes as a token upper bound and
the maximum output allowance. Charge uncached input at the greater input or
cache-write rate because no separately qualified cache-write receipt is assumed.
This is a conservative ledger estimate, not a provider invoice reconciliation.
Sources: [model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

Follow ADR-0105: serialize reservations and settings on the site row; record an
immutable request hash, role, release, recovery generation, UTC month and hold
before credential access or egress. Dispatch reauthorizes the current owner,
scope, month, cap and request hash. A reservation can dispatch only once. Failed,
invalid and unknown outcomes retain the hold; a validated usage receipt replaces
it, including overspend. There is no automatic refund or paid-network retry.
Only a pre-egress admission deferral may retry under the existing bound.
The default monthly OpenAI cap is USD 25, warning at 80 percent; exhaustion is
visibly unavailable. Existing weekly writer caps remain separate and unchanged.

Writing roles use plain, specific, approved-fact-only prompts, banned English
phrases, varied sentences, active voice and short paragraphs. Article voice snippets
are bounded style-only data: matching top completed-crawl pages ranked by the
latest final page-dimensional GSC clicks, otherwise owner-selected pages or the
Business Brain voice profile. Target-page language controls Telugu, English or
natural code-switching. A voice snippet never becomes an approved business fact.

Articles use outline, draft, rubric critique and revision, then deterministic
quality metrics. One failed quality attempt regenerates the entire bounded
four-pass sequence and consumes budget. A second failure produces an explicit
`low_quality` flag and reasons on the owner-required draft. Originality rejection
from 0082 still wins. Standalone technical metadata cannot silently seal a failed
quality result where no low-quality review surface exists: it returns unavailable.

## Limits

Metrics are versioned heuristics, not human writing certification. They measure
banned phrases, filler/hedges, repeated five-grams, sentence-length variance,
English passive/readability proxies and paragraph length. Variance, repetition
and readability thresholds apply only above 60 words. English passive and
readability metrics are explicitly not assessed for Telugu or mixed-language
text. Combining marks are kept in words; target-language mismatch adds a flag.

No existing unimplemented report writer is fabricated. Role configuration is
available for report text; reports currently derived deterministically from
committed projections remain deterministic. No new credential path, publishing
authority, autonomy eligibility, provider capability or production release is
created. First live use requires provider/privacy qualification and owner review
of a held-out multilingual writing set, not merely synthetic threshold tests.

See [0136 implementation](../implementation/0136-writing-quality.md) and
[0143 grounding](0143-claim-level-grounding.md).
