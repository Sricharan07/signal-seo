# ADR-0128: Preserve incomplete Bing generations at the measurement read port

Status: Accepted for internal composition; measurement integration deferred.
Date: 2026-10-02. Slice: 0129. Classification: security-critical.

## Context

0094 is in the merge train and must not be modified by this slice. It currently
marks per-page Bing unavailable. A provider read is not proof of complete demand,
and a date-bearing row with an undocumented aggregation period cannot safely be
treated as a daily measurement point (ADR-0127).

## Decision

Reuse `app.bing_import_generations`, with the distinct `page_performance` kind.
Keep the original 0071 recorder untouched. A new narrow SECURITY DEFINER recorder
accepts only strict bounded page rows and the exact current binding, checks
observed credentialed Bing-profile GET evidence, response digest, size and exact
GetPageStats query, and locks the binding against revocation before inserting.
Existing forced RLS and append-only protection remain in force; runtime roles
receive no table access. Migration 0079 follows 0078 in the accepted merge train.

`control.read_bing_page_performance(tenant_id, site_id)` is executable only by
the existing trusted `signal_crawl_ingest` role, like the existing internal
binding/import composition. It is not a human-facing API and tenant IDs are not
accepted from a browser. It returns at most the latest immutable generation for
the current verified binding, including original rows, coverage and digest.
Wrong tenant/site pairs, absent imports, expired origin verification and revoked
or replaced bindings yield no generation, never manufactured zero counts.
The Python `read_bing_page_performance` function exposes that port without I/O to
Bing or OpenBao. Coverage is returned verbatim, including future metadata.

## Follow-Up for 0094

After 0094 merges, integrate this port behind its existing trusted measurement
composition, carry the generation identity and coverage verbatim, and preserve
source separation. Do not substitute site totals, missing pages, click position,
or unknown time periods for page-impression position or daily observations.
Explicitly qualify the provider period/timezone semantics before admitting
date-window comparisons; unsupported comparisons remain unavailable. This slice
changes no measurement code, schedules, worker registration, dashboard, or
production configuration.

## Alternatives

Granting table SELECT would expose unrelated generations and weaken function-only
access. An owner/browser port is unnecessary for the downstream worker. Amending
0094 before merge would violate slice ownership. Returning all historical
bindings would revive revoked data availability and obscure provenance.

## Consequences and Verification

The trusted ingest role continues to address tenant/site work; this is not a
new end-user authorization mechanism. Tests cover mismatch, role denial,
immutable records, malformed rows, unsupported coverage, digest mismatch,
revocation during I/O and provider failure. See [0129](../implementation/0129-bing-pages.md)
and its evidence. Live Bing and production composition remain NOT_EXECUTED.
