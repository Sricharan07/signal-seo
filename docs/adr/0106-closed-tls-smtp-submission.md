# ADR-0106: Closed TLS SMTP Submission in Shared Egress

Status: Accepted for the internal optional email boundary

## Context

Revision 4.0 sections 12 and 18 require optional email without a dependency on
Signal-operated services. The shared gateway described by ADR-0083 previously
handled HTTP only; SMTP credentials must not acquire generic network authority.

## Decision

Add exactly one non-HTTP profile, `SMTP_SUBMIT`, implemented by
`SharedSmtpEgress` and a pinned SMTP submission transport. Operator configuration
fixes one host, one port, required STARTTLS or implicit TLS, normalized sender,
exact HTTPS dashboard origin, and a daily site cap. A configuration digest binds
every immutable outbox intent. Credentials are read only from OpenBao KV-v2
`signal-email/data/smtp/default`; PostgreSQL stores no username or password.

The profile cannot be used through the HTTP gateway. It screens all DNS answers
with the existing public-address policy, connects to a numeric admitted peer,
checks that peer, and verifies the TLS certificate and original hostname before
AUTH, MAIL, RCPT, or DATA. There is no plaintext or certificate fallback. DNS has
a three-second bound; SMTP has a fifteen-second deadline, bounded replies, and a
32-KiB message bound. The existing global origin bucket admits only the configured
`smtp+tls://host:port` identity, reserving thirty seconds between submissions.
Robots rules remain mandatory for HTTP; they do not authorize or apply to SMTP.

A durable dispatch receipt commits before network I/O. Definite transient
rejections have at most three attempts, five minutes apart, subject to current
recipient authority and the UTC daily cap on every attempt. Accepted, ambiguous,
and abandoned dispatches are never resent. A lost acknowledgement is `unknown`,
not success and not a retry. Receipts retain digests and response classes only.
Queue constraint failures are immutable audit events and cannot turn a notification
failure into a rollback of a pause or revocation.

## Alternatives

- A Signal relay would violate independent self-host operation.
- Generic sockets or SMTP through an HTTP profile would enlarge egress authority.
- Retrying ambiguous DATA outcomes would risk duplicate delivery.

## Consequences

The operator must supply a reachable public TLS submission endpoint, trust roots,
OpenBao custody, and a trusted host that pumps `deliver_due` using the current
external recovery generation. No new service or dependency is introduced. Missing
configuration, secrets, or composition is visibly unavailable. SMTP acceptance
does not prove inbox placement or reading. A deliberately conservative unknown
outcome may lose a message rather than duplicate it.

## Verification

See [0093](../implementation/0093-email-reports.md) and its evidence for real TLS
loopback SMTP protocol tests, real PostgreSQL dispatch/cap/receipt tests, real
OpenBao credential ACL tests, and the complete regression gate. Live provider
sending and deployed supervision remain `NOT_EXECUTED`.
