# Slice 0166 - Connector Framework

Classification: **Security-critical** (secrets, identity-derived connector
authority and restriction/recovery semantics). Base: `a519a65`.
[ADR-0173](../adr/0173-connector-framework.md).

## Scope And Contracts

One shared Python connector framework now owns SQL-row invocation, session/state
hashing, PKCE consumption, verifier cleanup, staged credential cleanup,
serialized refresh rotation, restriction outcome handling and upstream
revocation cleanup. GSC/Bing use shared connect/callback/confirm mechanics.
GA4/Docs retain their resource-specific flows over shared PKCE/KV mechanics.
GitHub read and PR extension, Slack, Telegram, DataForSEO, WordPress and Webflow
reuse the parts appropriate to their established authority/provider contracts.

OAuth secrets share KV-v2 storage, CAS-zero creation, permanent verifier
consumption, compare-and-swap refresh rotation and destruction. Namespace,
credential validator and fixed error codes remain adapter-owned; GA4/Docs retain
their isolated namespaces. Other connector secrets reuse active KV metadata
validation, including each adapter's original missing-field/version rules.

The duplicate-key JSON hook is shared by Webflow, integration scope, owner
connector HTTP, Slack HTTP, WordPress protocol and WordPress egress validation.
Only those existing duplicate-denying boundaries change implementation.

Revision 4.0 REQ-025/INV-029/INV-032/INV-033 and Revision 3.2
INV-002/003/005/021/025 and section 10 remain unchanged. Journal restriction
producers and SQL authority gates are unchanged. Pending outcomes never become
durable success. Credentials, OAuth codes and provider bodies are not added to
logs or projections. All existing HTTP bodies, routes, status fields and
dashboard contracts are unchanged. No SQL, migrations, dependencies, deployment
or production write enablement.

## Qualification

[Evidence](../evidence/0166-connector-framework.json) records exact counts.
Existing tests are unchanged. New framework tests cover nested duplicate keys,
callback denial before credentials/I/O, nonce bounds, verifier failure cleanup,
PKCE mismatch, staging failure cleanup, pending restrictions, and upstream
failure with active-secret removal.

Real disposable PostgreSQL qualification covers GSC, Bing, GA4, Docs, Slack,
Telegram and provider data. OpenBao checks exercise actual ACLs, namespace
isolation, CAS, permanent deletion and fixed failures. Webflow and WordPress
labs retain their journaled draft-only write qualification. Crawler network
checks retain real private-address/redirect denials. Provider doubles remain
behind shared egress; live provider runs are NOT_EXECUTED.

The initial temporary focused database invocation omitted migrations and failed
with missing tables/functions (1 pass, 9 failures, 57 setup errors); it is not
qualification. The corrected run migrates the disposable database first.
The first Webflow run passed 60 cases but its source-stability check failed
because formatting completed during the run; it is not a passing lab.

Full gate is reserved for the stacked 0167 tip, once, per owner instruction.
No Keycloak behavior was changed. Labs stop only their own randomly named
resources; no shared Docker state is stopped or pruned.

## First Live Run

Unchanged owner inputs: privately configured provider client/App/bot credentials
and TLS trust, exact verified site/resources, current owner/MFA and recovery
generation, consent/callback configuration, provider-specific permissions and
restriction-journal composition. Publishing remains disabled unless separately
qualified under the existing owning slice. This refactor grants no live authority.
