# ADR-0147: Exact Front-Matter Content Adapter

Status: accepted for internal qualification; live providers NOT_EXECUTED.
Slice: 0139. Classification: security-critical.

## Context

Markdown, MDX and Eleventy Nunjucks pages commonly store titles/descriptions in
front matter. Formatting, key order and content bodies must survive candidate
edits. A source file can produce multiple pages, so source shape cannot establish
low impact. Astro delivery already supplies exact build and fresh-MFA authority.

## Decision

Use one data-only adapter for a leading YAML (`---`), TOML (`+++`) or JSON
(`;;;`, JSON in `---`, or a leading JSON object) block. Pinned PyYAML 6.0.3 source
marks, standard-library tomllib validation and strict JSON decoding prove one
existing top-level string scalar. Only title/description are editable. Reparse
the result and prove unchanged non-target fields, exact scalar boundaries,
quoting style, line endings/indentation, and every byte outside that span. Never
emit or reserialize the block. Bodies and repository instructions stay data.

Reject duplicate/nested target keys, aliases, anchors, merge keys, explicit YAML
tags, missing/non-string fields and ambiguous syntax. Multiline edits retain line
count, indentation and block indicators. TOML edits admit bare root metadata keys
before tables or unrelated multiline structures. Unsupported syntax remains
unavailable, not normalized into apparent support.

Reuse Astro collection inference and 0127 all-page/artifact proofs. Require
separately reviewed signed F1 releases and fresh dashboard Owner Inbox approval
for A2 and A4. Never admit standing/weekly/autonomous eligibility. More than one
changed page is A4. Layouts, includes, data templates, configuration, workflows
and protected paths are excluded altogether, not merely reclassified.

## Alternatives

Whole-block serialization loses comments, quoting and key order. Regex-only
replacement cannot establish field identity or reject duplicate semantic keys.
New generator-specific approval/storage services duplicate authority and receipt
boundaries. All three are rejected.

## Consequences

Migration 0081 follows 0080 and extends function guards without adding tables or
editing existing migrations. F1 uses existing encrypted build, immutable impact
and MFA records. Strict API/dashboard contracts project that same scope.
Arbitrary metadata insertion is not provided. No production authority is enabled.

## Verification

Run [0139's commands](../implementation/0139-front-matter-content-adapter.md).
Parser/patch/reconstruction/injection and build-scope negatives supplement real
PostgreSQL Owner/tenant/role/replay/unprotected-base and offline container tests.
Qualification is recorded in [evidence](../evidence/0139-front-matter.json).
