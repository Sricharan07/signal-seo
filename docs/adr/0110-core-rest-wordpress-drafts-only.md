# ADR-0110: Keep WordPress Core REST Delivery Draft-Only

Status: Accepted for slice 0099's internal optional boundary.
Date: 2026-10-02.

## Context

Revision 4.0 sections 6.2, 12, 13 and 14 require exact authority for CMS writes
and publishing. Revision 3.2 section 19.3 forbids repeating an ambiguous external
write merely because its result is not yet observable. WordPress Core REST exposes
post creation and updates, but not an atomic compare-and-set on the sealed
revision or a native idempotency key. A read followed by a write cannot certify
an exact existing-post update or publish. See the official
[posts reference](https://developer.wordpress.org/rest-api/reference/posts/)
and [update implementation](https://developer.wordpress.org/reference/classes/wp_rest_posts_controller/update_item/).

## Decision

Deliver only one new draft post from one sealed Content Writer new-article result,
after the current owner approves its exact digest in the Inbox. Every outbound
POST targets only `/wp-json/wp/v2/posts`, has no query, and forces `status=draft`.
Existing-post updates, SEO-field updates, status changes, publishing, scheduling,
private posts, deletion and trashing have no delivery command or permitted write
route. Standing grants cannot authorize this owner-only subset.

Use a deterministic non-secret slug `signal-s<32 intent UUID hex>` as the
reconciliation marker. The independent write-intent journal is acknowledged
before dispatch. After an ambiguous POST, read only the exact slug, draft status,
bound author and edit context. One valid own-user match records the draft; zero
keeps the intent `outcome_unknown` and quarantined; multiple matches escalate.
Later owner-requested checks are limited to twelve, at least five minutes apart.
There is no implicit background scheduler or re-POST after ambiguity.

A single retry is possible only when shared admission deferred before dispatch,
or Core REST returned a narrowly classified, definitive pre-write rejection.
Other exceptions and responses, including generic 4xx errors, are ambiguous:
Core [creation](https://developer.wordpress.org/reference/classes/wp_rest_posts_controller/create_item/)
can insert a post before a subsequent metadata error. A fresh draft requires a
separate explicit owner action, new intent and new marker. The old quarantined
intent remains read-reconcilable and can still record a delayed appearance.

The API and dashboard expose these exact unavailable reasons:

- Existing updates: "WordPress core REST has no atomic preconditions; needs a certified bridge"
- Publishing: "Publish in WordPress yourself; Signal cannot publish an exact revision safely via core REST"

The owner may publish in WordPress independently. Signal can GET the recorded id,
compare title/content/excerpt to the sealed digest, record `edited_before_publish`
without blaming the owner, and optionally run the existing 0085 public GET
verification. Signal never changes that post.

## Alternatives

- A read-before-write timestamp check is rejected: the intervening race remains.
- Blind retry after an empty marker read is rejected: the first write may commit later.
- An owner-installed certified bridge with atomic preconditions and idempotency
  is deferred to a possible later slice. No bridge is built, installed, or
  represented as available here.

## Consequences And Verification

This is a bounded delivery subset, not complete WordPress or Publisher support,
and grants no R4 release or production authority. Core default `/?p=<id>` links
and pretty permalinks are supported on the bound HTTPS origin; preview/token
queries are not. Real PostgreSQL and a local stdlib REST double behind shared
egress test approval, forced draft, forbidden mutations, delayed commits,
duplicate matches, definitive rejection, pre-dispatch deferral, owner-created
fresh intent, and read-only observation. Evidence and live limitations are in
[0099](../implementation/0099-wordpress-drafts.md).
