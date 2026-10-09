import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TeamSettings } from "../components/team-settings";
import { InvitationRevocationConfirmation } from "../components/invitation-revocation";
import { revokeTeamInvitation, validateTeam } from "../lib/team-api";
import { POST } from "../app/auth/team/revoke/route";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const site = "11111111-1111-4111-8111-111111111111", invitation = "22222222-2222-4222-8222-222222222222";
const token = "t".repeat(43), csrf = "c".repeat(43), origin = "http://localhost:3000";
const item = { id: invitation, email: "invited@example.test", role_key: "viewer", state: "pending",
  created_at: "2026-10-04T00:00:00Z", expires_at: "2026-10-05T00:00:00Z", revocation_durability: null };
const projection = { schema_version: 1, site_id: site, can_revoke: true, truncated: false, members: [], invitations: [item] };
const options = { tenantToken: token, siteId: site, invitationId: invitation, confirmed: true, origin };

test("Revoke appears only for a pending invitation in a qualified owner projection", () => {
  const pending = renderToStaticMarkup(createElement(TeamSettings, { siteId: site, state: validateTeam(projection, site) }));
  assert.match(pending, />Revoke<\/button>/); assert.doesNotMatch(pending, /Revoke this invitation/);
  for (const state of ["accepted", "expired", "revoked"]) {
    const html = renderToStaticMarkup(createElement(TeamSettings, { siteId: site,
      state: validateTeam({ ...projection, invitations: [{ ...item, state, revocation_durability: state === "revoked" ? "pending" : null }] }, site) }));
    assert.doesNotMatch(html, />Revoke<\/button>/);
  }
  const unavailable = renderToStaticMarkup(createElement(TeamSettings, { siteId: site,
    state: validateTeam({ ...projection, can_revoke: false }, site) }));
  assert.doesNotMatch(unavailable, />Revoke<\/button>/);
});

test("confirmation names the consequence, allows cancellation and uses quiet danger styling", () => {
  const html = renderToStaticMarkup(createElement(InvitationRevocationConfirmation, { busy: false, onConfirm() {}, onCancel() {} }));
  assert.match(html, /shared link will stop working/); assert.match(html, /Current members keep their access/);
  assert.match(html, /Revoke invitation/); assert.match(html, /Cancel/); assert.match(html, /danger-command/);
  assert.doesNotMatch(html, /primary-command|Technical details|password/);
});

test("confirmed pending-recovery projection never claims durable revocation without a receipt", () => {
  const html = renderToStaticMarkup(createElement(TeamSettings, { siteId: site,
    state: validateTeam({ ...projection, invitations: [{ ...item, state: "revoked", revocation_durability: "pending" }] }, site) }));
  assert.match(html, /Revoked locally/); assert.match(html, /Recovery confirmation pending/);
  assert.doesNotMatch(html, /Recovery confirmation recorded/);
  for (const invalid of ["granted", "complete", null]) assert.equal(validateTeam({ ...projection,
    invitations: [{ ...item, state: "revoked", revocation_durability: invalid }] }, site).state, "unavailable");
});

test("revocation requires confirmation and exact identifiers before any I/O", async () => {
  let calls = 0;
  for (const invalid of [{ confirmed: false }, { invitationId: "bad" }, { siteId: "bad" }])
    assert.deepEqual(await revokeTeamInvitation({ ...options, ...invalid, fetcher: async () => { calls++; throw new Error("must not run"); } }), { state: "denied" });
  assert.equal(calls, 0);
});

test("revocation relays tenant CSRF and validates the exact resource and local outcome", async () => {
  let calls = 0;
  const result = await revokeTeamInvitation({ ...options, fetcher: async (url, init) => {
    if (++calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
    assert.equal(new URL(String(url)).pathname, `/v1/sites/${site}/invitations/${invitation}/revoke`);
    assert.equal(init?.body, "{}"); assert.equal(init?.redirect, "error");
    assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
    assert.equal(new Headers(init?.headers).get("origin"), origin);
    return Response.json({ schema_version: 1, site_id: site, invitation_id: invitation,
      revoked_at: "2026-10-04T00:00:00Z", durability: "AUTHORITY_DURABILITY_PENDING" });
  } });
  assert.equal(calls, 2); assert.deepEqual(result, { state: "revoked" });
});

test("denied, unavailable and malformed revoke responses never imply a change", async () => {
  for (const status of [403, 503]) {
    let calls = 0;
    assert.deepEqual(await revokeTeamInvitation({ ...options, fetcher: async () => ++calls === 1 ?
      Response.json({ schema_version: 1, csrf_token: csrf }) : Response.json({}, { status }) }), { state: status === 403 ? "denied" : "unconfirmed" });
  }
  let calls = 0;
  assert.deepEqual(await revokeTeamInvitation({ ...options, fetcher: async () => ++calls === 1 ?
    Response.json({ schema_version: 1, csrf_token: csrf }) : Response.json({ schema_version: 1, site_id: invitation,
      invitation_id: invitation, revoked_at: "2026-10-04T00:00:00Z", durability: "ACKNOWLEDGED" }) }), { state: "unconfirmed" });
});

test("dashboard revoke route rejects missing or cross-origin browser proof without cookies", async () => {
  const cases: HeadersInit[] = [{}, { Origin: "https://evil.test", "Sec-Fetch-Site": "cross-site", Cookie: `${TENANT_COOKIE_NAME}=${token}` }];
  for (const headers of cases) {
    const response = await POST(new Request(`${origin}/auth/team/revoke`, { method: "POST", headers }));
    assert.equal(response.status, 403); assert.equal(response.headers.getSetCookie().length, 0);
  }
});

test("dashboard revoke route rejects missing confirmation before fetching", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new Error("must not run"); };
    const response = await POST(new Request(`${origin}/auth/team/revoke`, { method: "POST",
      headers: { Origin: origin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json", Cookie: `${TENANT_COOKIE_NAME}=${token}` },
      body: JSON.stringify({ site_id: site, invitation_id: invitation, confirmed: false }) }));
    assert.equal(response.status, 422); assert.deepEqual(await response.json(), { state: "denied" });
    assert.equal(response.headers.get("cache-control"), "no-store");
  } finally { globalThis.fetch = original; }
});
