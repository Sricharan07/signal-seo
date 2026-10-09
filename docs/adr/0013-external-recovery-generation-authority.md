# ADR-0013: Anchor Recovery Generation Outside The Business Database

Status: Accepted. Date: 2026-09-08.

## Context

Every application session records the recovery generation under which it was
issued. Authorization rejects a session when that value differs from the current
generation. The comparison prevents a restored business-database backup from
silently reviving access only if the current value comes from a recovery boundary
that is not restored with that database.

Slices 0004 and 0010 deliberately accepted an externally obtained generation.
Slice 0012 left acquisition to its caller. That placeholder was unsuitable for a
future HTTP adapter because a caller could accidentally source both values from
the restored database or accept an unvalidated deployment string.

Sources reviewed on 2026-09-08:

- [OpenBao KV version 2](https://openbao.org/docs/secrets/kv/kv-v2/)
- [OpenBao path-based policies](https://openbao.org/docs/2.5.x/concepts/policies/)

## Decision

Store the current recovery generation at exactly
`signal-authority/data/recovery/current` in a separate OpenBao KV v2 mount. Give
the login workload a dedicated token with only `read` on that exact path. It has
no glob, list, create, update, patch, delete, metadata, policy, mount, or token
capability. The PKCE credentials remain separate and cannot acquire recovery
authority through this client contract.

`OpenBaoRecoveryAuthority` accepts an exact HTTPS origin, a bounded token and
mount name, and mandatory TLS verification. It follows no redirects or environment
proxy, uses a five-second timeout, and reads at most 16 KiB. It accepts only a 200
JSON object containing exactly one bounded generation string in a live,
non-deleted KV version with a positive integer version. Provider responses and
transport details are reduced to fixed error codes.

The login coordinator reads the generation after local callback validation and
before atomically consuming the PostgreSQL login attempt. If the authority is
unavailable, TLS is disabled, or its record is malformed or deleted, login fails
closed and the callback remains retryable. Once the attempt is consumed, the
existing proof-burning order from ADR-0012 remains unchanged. The generation
value, not OpenBao's internal version number, is written to the new session.

The OpenBao HTTP mechanics are shared with the PKCE client so both clients retain
one reviewed timeout, origin, TLS, response-size, and JSON contract. The clients
still expose different operations and credentials.

This decision supersedes only ADR-0012's caller-supplied recovery-generation
placeholder and callback ordering before PostgreSQL consumption. All other
ADR-0012 decisions remain accepted.

## Consequences

A business-database restore cannot derive the current generation from its own
session rows. Login requires a live read from an independently restored authority
before consuming one-time callback state. An authority outage therefore blocks
new sessions but does not burn valid callbacks.

The generation identifies a recovery epoch and is not a bearer credential, but
it is still omitted from evidence because operational values are unnecessary for
qualification. The OpenBao token is sensitive, hidden from object representations,
and never included in an error or report.

This slice implements only the read boundary. It does not implement generation
rotation, disaster-recovery orchestration, restriction journals, OpenBao
authentication, seal management, HA storage, replication, backup, restore, audit
devices, monitoring, or production provisioning. Existing authorization and
tenant-session functions still receive the current generation from their trusted
future service adapters.

## Alternatives

Reading the value from PostgreSQL was rejected because it shares the recovery
failure domain the generation is intended to fence. An environment variable was
rejected because replica drift and ad hoc deployment updates are hard to reconcile
or audit. Giving the login process write permission was rejected because ordinary
authentication must not rotate recovery authority. Reusing the short-lived PKCE
mount was rejected because its one-version, automatic-deletion policy is wrong for
a durable recovery epoch with operator-controlled history.

## Verification

Thirty-three focused authority cases cover exact requests, strict response shape,
deleted and destroyed versions, size bounds, mandatory TLS, redaction, and
configuration validation. The real TLS OpenBao 2.6.1 lab reads the synthetic
generation with the exact scoped token and observes a 403 when that token attempts
CAS mutation. Its seven scenarios also rerun the PKCE boundary. The cumulative
PostgreSQL suite passes 155 cases, including malformed and unavailable authority
data before callback consumption. Both reports bind the result to exact source
hashes, use synthetic data, record no credentials or generation value, and confirm
cleanup.
