# ADR-0011: Keep PKCE Verifiers In A Narrow OpenBao Boundary

Status: Accepted. Date: 2026-09-07.

## Context

The durable OIDC login-attempt record intentionally stores only a reference to a
PKCE verifier. The verifier is a short-lived bearer secret: anyone who obtains it
together with an intercepted authorization code can attempt the code exchange.
Putting it in PostgreSQL would mix recoverable business records with reusable
secret material and broaden database-backup exposure.

The secret operation also has difficult failure states. A duplicate initiation
must not overwrite an earlier verifier. A callback must not receive a verifier if
permanent removal is unconfirmed. A process failure after durable login-attempt
consumption may sacrifice that login, but it must not reopen the attempt or make a
secret reusable.

Sources reviewed on 2026-09-07:

- [OpenBao server command and TLS development mode](https://openbao.org/docs/commands/server/)
- [OpenBao development-server warning](https://openbao.org/docs/concepts/dev-server/)
- [OpenBao KV v2 behavior and ACL paths](https://openbao.org/docs/secrets/kv/kv-v2/)
- [OpenBao policies](https://openbao.org/docs/2.5.x/concepts/policies/)
- [OpenBao 2.6.1 container package](https://github.com/openbao/openbao/pkgs/container/openbao/versions?filters%5Bversion_type%5D=tagged)

## Decision

Store each verifier in a dedicated OpenBao KV v2 mount under a UUIDv4-derived
`oidc-login` path. PostgreSQL receives only a strict
`secret://oidc-login/<uuid>/1` reference. Require CAS zero and version one so a
new login can create a secret but cannot overwrite an existing path. Configure
the mount with required CAS, one retained version, and a ten-minute automatic
soft-deletion deadline. Permanent consumption deletes all key metadata and
versions before the verifier is returned to the callback process.

Use separate OpenBao credentials. The initiation credential has only `create` on
the PKCE data path. The callback credential has only `read` on that data path and
`delete` on its metadata path. Neither can list keys; the writer cannot read or
delete, and the consumer cannot create or update. Production authentication for
these workloads is deferred to deployment design rather than embedding static
OpenBao tokens in configuration or source.

The client accepts only an exact HTTPS origin, a fixed safe mount name, strict
UUIDv4 references, and RFC 7636 verifier syntax. It uses a five-second timeout,
no redirects, no environment proxy, mandatory TLS verification, and a 16 KiB
response limit. All provider and transport failures become fixed codes without
response bodies, tokens, references, or verifier values.

The one-time concurrency gate remains PostgreSQL's atomic
`consume_oidc_login_attempt`. The winning callback receives the reference and may
then call OpenBao. The secret client is not independently an atomic read-and-delete
queue: two callers that bypass the durable gate could both read before deletion.
If database consumption, OpenBao read, deletion, or code exchange fails, that
login attempt is abandoned and the user starts a fresh login. It is never revived.

## Consequences

Business-database rows and backups no longer need the PKCE verifier. A create-only
credential plus CAS prevents overwrite, while the callback does not receive a
verifier until permanent removal is confirmed. Sequential replay fails after
consumption. A lost create response can leave an unreachable short-lived secret;
the mount deadline and an operational cleanup process limit that residue.

OpenBao KV's automatic `delete_version_after` is a soft deletion, not immediate
physical destruction. The normal consume path therefore deletes metadata
explicitly. OpenBao storage encryption, audit devices, snapshots, replication,
and underlying-media behavior still govern physical persistence and must be
covered by production operations and retention policy.

The disposable lab uses OpenBao development TLS, an in-memory store, and a root
token only to provision synthetic scoped credentials. Its root token is visible
to Docker administrators and its generated keys are not production material.

## Alternatives

Storing the verifier in PostgreSQL was rejected because it broadens secret and
backup exposure. An in-process cache was rejected because callbacks must survive
process restart and may land on another replica. Giving one credential full KV
CRUD or list permission was rejected as unnecessary. Returning the verifier after
a soft delete was rejected because KV v2 can undelete soft-deleted data. OpenBao
response wrapping remains useful for other delivery patterns, but it does not
replace the durable callback winner and reference lifecycle required here.

## Verification

A digest-pinned OpenBao 2.6.1 container passed five TLS-backed scenarios: bounded
KV v2 configuration, CAS-zero creation, disjoint writer/consumer ACLs, permanent
consume with replay rejection, and rejected overwrite with original preservation.
Forty-eight client tests and eleven lab tests cover validation, exact requests,
response bounds, redaction, failure semantics, cleanup, policy shape, and evidence
sanitization. The evidence records no production authority or secret material.
