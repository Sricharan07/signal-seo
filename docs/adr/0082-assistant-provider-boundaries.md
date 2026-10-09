# ADR-0082: Independent Assistant Search Provider Boundaries

Status: accepted for the local R1 provider boundary, 2026-09-29.

## Context

R1 requires evidence from OpenAI, Perplexity, and Gemini without letting an
unconfigured provider appear ready, leaking credentials, or treating consumer
assistant pages as sources. The next slice will own target questions and citation
interpretation; this slice owns only transport, credential, and raw-response
evidence boundaries.

## Decision

Each provider has an independent read-only OpenBao KV-v2 `api_key` at
`signal-assistants/data/<provider>/default`. A scoped reader cannot write. A
missing, malformed, or inaccessible secret makes only that provider unavailable.
No API key is stored in PostgreSQL, a URL, an egress digest, or a qualification
report. The internal adapter uses fixed official API origins, a fixed current
model or preset, web-search tooling, bounded question/request/response sizes,
and provider-specific model-purpose egress profiles under ADR-0083. The
OpenAI, Perplexity, and Gemini profiles each fix a HTTPS origin, POST/JSON
shape, credential header, JSON response, and size and time bounds. Gemini's
`x-goog-api-key` header is accepted only for its fixed HTTPS model origin.
The profile is bound to the immutable dispatch before I/O, and the
connector-purpose binder refuses these assistant identities. A response must carry
provider identity and a completed result before its digest and exact bounded
JSON can be appended to forced-RLS immutable evidence. SQL requires a matching
observed credentialed model POST with the matching assistant profile at the
provider endpoint and a verified site.

All three public capability entries remain disabled, including when an OpenBao
key is configured. There is no autonomous trigger, target-question set, citation
parse, dashboard visibility baseline, or production admission in this slice.

## Alternatives

- Scraping consumer apps or search-result pages would violate the product
  safety contract; official provider APIs are the only source.
- One shared credential or a fallback provider would hide missing coverage and
  violate INV-032, so each provider fails independently.
- Parsing citation truth in this boundary would couple interpretation to
  transport; raw provider evidence remains immutable for 0075 to interpret.

## Consequences

Three provider accounts and API keys are needed for live qualification, and
the bounded searches may incur usage charges. Local labs use synthetic keys
and isolated transport only. Live success is `NOT_EXECUTED`.

## Verification

Commands and limitations are in [slice 0074](../implementation/0074-assistant-provider-boundaries.md).
The contracts are the [OpenAI Responses web-search guide](https://developers.openai.com/api/docs/guides/tools-web-search),
[Perplexity Agent API guide](https://docs.perplexity.ai/docs/agent-api/quickstart),
and [Gemini Google Search grounding guide](https://ai.google.dev/gemini-api/docs/generate-content/google-search).
