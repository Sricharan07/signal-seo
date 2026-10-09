# Local Pilot Runbook

This runbook starts Signal's disposable owner journey on real local dependencies.
It is for development and browser qualification only.

## Preconditions

- Docker Desktop is running.
- Node.js 22 dependencies and the repository Python environment are installed.
- Loopback API port `8000` and at least one dashboard port, `3000` or `3001`, are
  free.
- At least 3 GiB of disk space is available for the invocation-owned providers.

## Start

From the repository root:

```sh
npm run pilot
```

The identity, Work, and Pages steps run without a model credential. To enable the
Luna proposal step, inject a developer key without saving it in `.env` or shell
history:

```sh
read -rs SIGNAL_OPENAI_API_KEY
SIGNAL_OPENAI_API_KEY="$SIGNAL_OPENAI_API_KEY" npm run pilot
unset SIGNAL_OPENAI_API_KEY
```

The pilot removes the variable from its environment immediately, writes the
credential to a separate invocation-owned OpenBao mount with one exact read-only
client, and destroys that state at shutdown. Never use a customer credential.

Wait for `Signal local pilot is ready`, then open the exact dashboard URL printed
by the command. The pilot prefers `http://localhost:3000` and automatically uses
`http://localhost:3001` when port `3000` belongs to another process. To require
one exact allowlisted port instead of using fallback selection, start with:

```sh
SIGNAL_LOCAL_PILOT_DASHBOARD_PORT=3001 npm run pilot
```

Use only the synthetic credentials printed by the command:

```text
Username: signal-local-owner
Password: signal-local-only-password
```

The startup creates a fresh database and identity realm. Previous pilot data is
not reused.

## Optional Verified Crawl

The default `npm run pilot` still uses a synthetic, no-network Work activity.
To exercise the real crawl on a public HTTPS origin that you control, start a
separate disposable invocation:

```sh
npm run pilot:verified-crawl
```

Sign in with the same synthetic local owner, add your site, then use **Connectors**
to prove the exact origin. **Work** remains disabled until proof is current. Select
**Start audit** only after checking that the entered origin is yours and may be
crawled. This mode fetches `/robots.txt` and admitted same-origin links through
the pinned public-address and shared global-origin boundaries. It never signs in,
submits forms, follows outbound links, scrapes search results, or writes to the
site. The crawl is bounded by 1,000 discovered URLs, depth 8, 100 MiB of page
bodies, and one hour. `complete` means the bounded link-discovered frontier was
settled; it does not prove that unlinked or sitemap-only pages were discovered.

The encrypted artifact directory and key exist only for this invocation and are
destroyed when it stops. Do not use customer credentials, customer data, or an
origin you do not control. This mode is a local integration path, not production
readiness or a substitute for a real owner-origin release qualification.

## Qualified Browser Journey

1. Select **Sign in** and authenticate through the local Keycloak page.
2. Select **Local pilot workspace (synthetic identity)**.
3. Add one disposable site name and one canonical public HTTPS origin.
4. Confirm the Overview shows that exact site as current, `onboarding`, and
   ownership `unverified`.
5. Open **Work** and select **Start audit**.
6. Observe the page move through request, queue, execution, and result without a
   manual refresh.
7. Confirm the terminal receipt shows `Completed`, complete coverage, one
   synthetic URL, one terminal URL, and a bounded manifest identity.
8. Open **Pages** and confirm the committed manifest identity, digest, coverage,
   scope release, and crawl-policy release are visible.
9. To test the real read path, use an origin you control. Open **Connectors**,
   issue the exact ownership challenge, publish its plaintext value at the shown
   well-known path, and select **Verify origin**.
10. Return to **Pages** and select **Analyze verified homepage**. Confirm the
    final URL, HTTP status, title, first H1, meta-description state, evidence UUID,
    body digest, and observation time match the owner-controlled page. A missing
    non-empty description must produce one deterministic finding; a present
    description must not invent one.
11. Open **Signal Chat** and select **Prepare proposal** on the bounded typed
    command. Confirm the response identifies the Luna drafting responsibility,
    deterministic checks, the exact verified-homepage evidence, and no external
    write.
12. Open **Approvals** and inspect the before/after metadata, evidence UUID,
    model receipt and hashes, token usage, four checks, local A1 authority,
    one-cent cost ceiling, expiry, recovery statement, and complete revision
    SHA-256.
13. Select **Approve exact revision**, **Request edits**, or **Reject**. Confirm the
    immutable result appears and states that no external operation was dispatched.
14. Refresh Approvals. Confirm the same decision remains and no decision controls
    can replace it.

For an origin you control, the Connectors page can issue the exact ownership
challenge. Publish the displayed plaintext value at
`/.well-known/signal-site-verification.txt`, then select **Verify origin**. The
pilot resolves A/AAAA records through its bounded resolver, rejects every
non-public answer, pins one admitted address, validates TLS for the original
hostname, and permits no redirect. Do not attempt verification for an origin you
do not control. In the default pilot, this proof authorizes only the separately
requested bounded homepage read and no crawl or write. In the explicit
verified-crawl mode it also permits the separately requested Work crawl, with no
external write.

The default Work operation uses the real PostgreSQL command/outbox records, local
Temporal server, workflow, worker, and terminal projection. Its activity
deliberately returns synthetic evidence and never resolves or requests the entered
origin. The explicit verified-crawl mode instead registers the real executor and
records an immutable crawl manifest; the Pages view does not yet browse every
per-page crawl record.

The primary Pages action first commits an immutable intent, then reads only the
verified homepage through the pinned HTTP boundary. It stores bounded metadata and
the body digest as immutable PostgreSQL evidence tied to the completed command and
manifest. Known failures remain durable but produce no evidence or finding. This
qualifies one homepage metadata observation, not broad crawl coverage. The fixed
checked-in HTML fixture remains available only as a separate pipeline test.

Signal Chat and Approvals use that same verified-homepage evidence. One bounded
`gpt-5.6-luna` responsibility drafts the metadata through strict structured output
with no tools and `store=false`. PostgreSQL records the intent before provider I/O,
then stores the exact provider receipt, usage, output, and hash identities and
seals them into the RFC 8785 revision, 24-hour request, and one exact owner decision.
Approval accepts only the local draft. It does not create an outbox message,
GitHub request, provider operation, deployment, or customer change.

## Stop And Cleanup

Press Ctrl-C in the pilot terminal. A normal shutdown exits with status zero and
removes the invocation-owned PostgreSQL, OpenBao, and Keycloak containers. Confirm
ports are released when diagnosing cleanup:

```sh
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:3001 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

The selected dashboard port and API port should return no listener. An unrelated
listener on the unselected dashboard port remains untouched. Never remove unrelated
Docker resources; the lab cleanup is label- and invocation-bound.

## Failure Interpretation

| Failure | Meaning | Action |
| --- | --- | --- |
| Dashboard ports unavailable | Other local processes own both 3000 and 3001, or the explicitly selected port | Stop one process intentionally or free the selected port; Signal never stops it |
| API port unavailable | Another local process owns 8000 | Stop that process intentionally, then restart the pilot |
| Docker or space preflight fails | A real dependency cannot start safely | Restore Docker health or disk headroom; do not bypass the check |
| Identity not ready | Keycloak, OpenBao, discovery, or database readiness failed | Stop the run and inspect sanitized terminal status; restart from a fresh invocation |
| Callback rejected | State, nonce, PKCE, cookie, issuer, token, or session policy rejected | Do not retry around the check; run the focused identity and dashboard tests |
| Site creation rejected | Current owner/session/version or bounded site input was invalid | Refresh the page and inspect the explicit product state |
| Audit remains queued | The outbox publisher, Temporal server, or workflow worker is unavailable | Keep the external-write gate closed; inspect the pilot process and restart the disposable run |
| Work state unavailable | The latest authenticated projection was rejected or could not be read | Refresh once, then run the dashboard/API/database focused tests; do not infer completion |
| Fixture analysis not ready | No completed current-user audit exists for the selected site | Complete the Work audit, then retry from Pages |
| Fixture evidence conflict | A committed command/source digest differs from the fixed detector input | Stop; preserve the existing evidence and run the focused database/API tests |
| Proposal not ready | No current verified-homepage finding with a title or H1 supports the closed proposal | Verify and analyze the homepage from Pages, then retry from Signal Chat |
| Model not configured | The pilot was started without a model credential | Restart with a privately injected developer key; do not place it in source, `.env`, logs, or browser state |
| Model outcome unknown | Provider transport or post-call persistence was ambiguous | Do not retry blindly; preserve the run and inspect durable model-call state |
| Approval state changed | The request expired, its revision digest differs, or a decision already exists | Refresh Approvals and inspect the current immutable state; do not retry with altered fields |

## Security Boundary

- All provider ports bind to loopback or private invocation-owned networks.
- Dashboard fallback is limited to exact ports `3000` and `3001`; both exact OIDC
  callbacks are pre-registered, while the running API accepts only the selected
  dashboard origin.
- All identity credentials are fixed synthetic local fixtures. The optional model
  key is a developer-supplied secret isolated behind exact read-only OpenBao access.
- Local HTTP cookie names are distinct and activate only with the explicit
  nonproduction pilot flag. Every invocation gets a fresh cookie namespace;
  production cookie policy is not weakened.
- The default local workflow activity performs no DNS lookup, HTTP request,
  provider call, customer-data read, or external mutation. The explicit
  verified-crawl activity makes bounded GET requests only after current proof.
- The optional ownership action performs one bounded plaintext GET against the
  exact developer-controlled public HTTPS origin. It does not enter the audit or
  finding pipeline.
- The fixed local finding detector accepts no browser-provided HTML, URL, detector,
  or source identity and is retained only for internal pipeline qualification.
- The verified-homepage proposal accepts no browser-provided prompt, copy, target, evidence,
  role output, model, credential, cost, authority, or recovery behavior. The browser submits only the
  current site for preparation and the exact request, revision digest, decision
  identity, and closed decision value for approval.
- Proposal decisions create no outbox work and no external authority.
- No customer secrets, customer data, public test service, host-wide mount, or
  external write authority is used.
- Shutdown destroys state; this is not backup, restore, or durability evidence.
