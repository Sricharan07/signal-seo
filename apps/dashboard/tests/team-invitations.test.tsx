import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TeamSettings } from "../components/team-settings";
import { InvitationAcceptance } from "../components/invitation-acceptance";
import { InvitationTransition } from "../components/invitation-transition";
import { DashboardView } from "../components/dashboard-view";
import { acceptSignalInvitation, beginSignalLogin, completeSignalLogin, IDENTITY_COOKIE_NAME,
  INVITATION_IDENTITY_COOKIE_NAME, OIDC_BINDING_COOKIE_NAME, TENANT_COOKIE_NAME, invitationFinishPath } from "../lib/browser-auth";
import { invitationCredential, linkCookie, readLinkCookie } from "../lib/invitation-route";
import { issueTeamInvitation, loadTeam, validateTeam } from "../lib/team-api";
import { POST as verify } from "../app/auth/invitations/verify/route";
import { POST as accept } from "../app/auth/invitations/accept/route";
import { POST as join } from "../app/auth/invitations/join/route";
import { POST as finish } from "../app/auth/invitations/finish/route";
import { POST as issue } from "../app/auth/team/route";

const site = "11111111-1111-4111-8111-111111111111", tenant = "22222222-2222-4222-8222-222222222222";
const invitation = "33333333-3333-4333-8333-333333333333", user = "44444444-4444-4444-8444-444444444444";
const token = "t".repeat(43), csrf = "c".repeat(43), credential = `${invitation}.${token}`;
const origin = "http://localhost:3000", baseUrl = "https://api.example.test", provider = "https://identity.example.test";
const team = { schema_version: 1, site_id: site, can_revoke: false, truncated: false,
  members: [{ user_id: user, display_name: "A teammate", role_key: "editor" }],
  invitations: [{ id: invitation, email: "invited@example.test", role_key: "viewer", state: "pending",
    created_at: "2026-10-04T00:00:00Z", expires_at: "2026-10-05T00:00:00Z", revocation_durability: null }] };
test("viewers see no Team controls, even if team data is accidentally provided", () => {
  const html = renderToStaticMarkup(<DashboardView snapshot={{ fetchedAt: "2026-10-04T00:00:00Z", connection: "connected", dependencies: "not_ready",
    inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] }}
    session={{ state: "authenticated", tenantId: tenant, role: "viewer", authenticationLevel: "primary",
      expiresAt: "2026-10-05T00:00:00Z", sessionVersion: 2, activeSiteId: site }} sites={{ state: "not_authenticated" }}
    organizations={{ state: "absent" }} authNotice={null} activeSection="settings" team={validateTeam(team, site)} />);
  assert.doesNotMatch(html, /team-title|Create invitation|invited@example.test|A teammate/);
});
function cookie(name: string, value: string, age = 300) {
  return `${name}=${value}; Path=/; Max-Age=${age}; HttpOnly; Secure; SameSite=Lax`;
}
function redirect(path: string, cookies: string[]) {
  const headers = new Headers({ Location: path });
  cookies.forEach(value => headers.append("Set-Cookie", value));
  return new Response(null, { status: 303, headers });
}

test("team settings show roles and invitation states without tokens or owner grants", () => {
  const html = renderToStaticMarkup(createElement(TeamSettings, { state: validateTeam(team, site), siteId: site }));
  assert.match(html, /A teammate/); assert.match(html, /Pending/); assert.match(html, /Create invitation/);
  assert.match(html, /class="technical-details"/); assert.match(html, /class="primary-command"/);
  assert.doesNotMatch(html, /value="owner"|Revoke|not_emailed|token_hash/);
});
test("unavailable team has no fake members or invitation form", () => {
  const html = renderToStaticMarkup(createElement(TeamSettings, { state: { state: "unavailable" }, siteId: site }));
  assert.match(html, /could not be loaded/); assert.match(html, /Reload team/); assert.doesNotMatch(html, /<form|<input|primary-command/);
});
test("team parser fails closed on wrong site, tokens, escalation, unbounded and invented states", () => {
  for (const invalid of [{ ...team, site_id: tenant }, { ...team, token }, { ...team, can_revoke: "true" },
    { ...team, invitations: [{ ...team.invitations[0], role_key: "owner" }] },
    { ...team, members: Array(101).fill(team.members[0]) },
    { ...team, invitations: [{ ...team.invitations[0], state: "delivered" }] }]) {
    assert.deepEqual(validateTeam(invalid, site), { state: "unavailable" });
  }
});
test("team load never converts denial or failure into an empty successful list", async () => {
  for (const status of [403, 500, 503]) assert.deepEqual(await loadTeam({ tenantToken: token, siteId: site,
    fetcher: async () => new Response(null, { status }) }), { state: "unavailable" });
  const loaded = await loadTeam({ tenantToken: token, siteId: site, fetcher: async (_url, init) => {
    assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store"); return Response.json(team);
  } });
  assert.equal(loaded.state, "available");
});
test("issuance relays browser proof and creates a fragment-only share link", async () => {
  let calls = 0;
  const result = await issueTeamInvitation({ tenantToken: token, siteId: site, email: "invited@example.test", roleKey: "viewer", origin,
    fetcher: async (_url, init) => {
      calls++;
      if (calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
      assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
      assert.equal(new Headers(init?.headers).get("origin"), origin);
      assert.deepEqual(JSON.parse(String(init?.body)), { email: "invited@example.test", role_key: "viewer" });
      return Response.json({ schema_version: 1, invitation_id: invitation, site_id: site, token,
        expires_at: "2026-10-05T00:00:00Z", delivery: "not_emailed" }, { status: 201 });
    } });
  assert.equal(result.state, "created");
  if (result.state === "created") { const link = new URL(result.link); assert.equal(link.search, ""); assert.equal(link.hash, `#${credential}`); }
});
test("issuance rejects owner role without I/O and never assumes provider delivery", async () => {
  const options = { tenantToken: token, siteId: site, email: "invited@example.test", roleKey: "owner", origin,
    fetcher: async () => { throw new Error("must not run"); } };
  assert.deepEqual(await issueTeamInvitation(options), { state: "denied" });
  for (const delivery of ["sent", "accepted", "delivered"]) {
    let calls = 0;
    assert.deepEqual(await issueTeamInvitation({ ...options, roleKey: "viewer", fetcher: async () => ++calls === 1 ?
      Response.json({ schema_version: 1, csrf_token: csrf }) : Response.json({ schema_version: 1, invitation_id: invitation,
        site_id: site, token, expires_at: "2026-10-05T00:00:00Z", delivery }, { status: 201 }) }), { state: "unconfirmed" });
  }
});
test("link context is bounded, duplicate-proof, HttpOnly and short lived", () => {
  assert.equal(invitationCredential(credential), credential);
  assert.equal(invitationCredential(`${credential}&tenant=${tenant}`), null);
  assert.match(linkCookie(credential), /Max-Age=600; HttpOnly; Secure; SameSite=Lax/);
  assert.equal(readLinkCookie(linkCookie(credential)), credential);
  assert.equal(readLinkCookie(`${linkCookie(credential)}; ${linkCookie(credential)}`), null);
  assert.doesNotMatch(linkCookie(null), new RegExp(token));
});
test("invitation verification uses purpose-bound route without token in return path", async () => {
  const result = await beginSignalLogin({ baseUrl, identityProviderOrigin: provider, invitation: true,
    returnPath: "/invitations/accept", fetcher: async (url) => {
      const destination = new URL(String(url)); assert.equal(destination.pathname, "/v1/invitations/verify");
      assert.equal(destination.searchParams.get("return_path"), "/invitations/accept"); assert.ok(!destination.href.includes(token));
      return redirect(`${provider}/authorize`, [cookie(OIDC_BINDING_COOKIE_NAME, token), cookie(INVITATION_IDENTITY_COOKIE_NAME, '""', 0)]);
    } });
  assert.equal(result.state, "redirect");
});
test("callback relays only invitation proof cookies for invitation-purpose completion", async () => {
  const result = await completeSignalLogin({ baseUrl, state: token, code: "code", browserBinding: token,
    fetcher: async () => redirect("/invitations/accept", [cookie(OIDC_BINDING_COOKIE_NAME, '""', 0), cookie(INVITATION_IDENTITY_COOKIE_NAME, token)]) });
  assert.equal(result.state, "redirect"); assert.equal(result.state === "redirect" ? result.location : null, "/invitations/accept");
  const forged = await completeSignalLogin({ baseUrl, state: token, code: "code", browserBinding: token,
    fetcher: async () => redirect("/invitations/accept", [cookie(IDENTITY_COOKIE_NAME, token), cookie(TENANT_COOKIE_NAME, token)]) });
  assert.equal(forged.state, "failed");
});
test("post-acceptance callback accepts only exact tenant/site destinations", async () => {
  const path = `/invitations/finish?tenant=${tenant}&site=${site}`;
  assert.equal(invitationFinishPath(path), true);
  for (const invalid of [`${path}&token=${token}`, path.replace(site, "invalid"), `//evil.test${path}`]) assert.equal(invitationFinishPath(invalid), false);
  const result = await completeSignalLogin({ baseUrl, state: token, code: "code", browserBinding: token, fetcher: async () =>
    redirect(path, [cookie(IDENTITY_COOKIE_NAME, token), cookie(TENANT_COOKIE_NAME, '""', 0), cookie(OIDC_BINDING_COOKIE_NAME, '""', 0), cookie(INVITATION_IDENTITY_COOKIE_NAME, '""', 0)]) });
  assert.equal(result.state, "redirect");
});
test("acceptance relays exact token and proof then clears proof without issuing a session", async () => {
  let calls = 0;
  const result = await acceptSignalInvitation({ baseUrl, identityProof: token, invitationId: invitation, token, displayName: "A teammate", dashboardOrigin: origin,
    fetcher: async (_url, init) => {
      if (++calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
      assert.deepEqual(JSON.parse(String(init?.body)), { invitation_id: invitation, token, display_name: "A teammate" });
      assert.equal(new Headers(init?.headers).get("cookie"), `${INVITATION_IDENTITY_COOKIE_NAME}=${token}`);
      return Response.json({ schema_version: 1, invitation_id: invitation, tenant_id: tenant, site_id: site,
        user_id: user, role_key: "viewer", accepted_at: "2026-10-04T00:00:00Z" }, { headers: { "Set-Cookie": cookie(INVITATION_IDENTITY_COOKIE_NAME, '""', 0) } });
    } });
  assert.equal(result.state, "redirect"); assert.equal(result.cookies.length, 1);
  assert.ok(!JSON.stringify(result).includes(token));
});
test("acceptance failures, replay and mismatched identity never claim acceptance", async () => {
  for (const status of [401, 403, 503]) {
    let calls = 0;
    const result = await acceptSignalInvitation({ baseUrl, identityProof: token, invitationId: invitation, token, displayName: "A teammate", dashboardOrigin: origin,
      fetcher: async () => ++calls === 1 ? Response.json({ schema_version: 1, csrf_token: csrf }) : Response.json({}, { status }) });
    assert.equal(calls, 2); assert.notEqual(result.state, "redirect"); assert.deepEqual(result.cookies, []);
  }
});
test("acceptance copy grants nothing for expired, used, revoked or mismatched invitations", () => {
  const html = renderToStaticMarkup(createElement(InvitationAcceptance, { ready: true, notice: "denied" }));
  assert.match(html, /expired/); assert.match(html, /used/); assert.match(html, /revoked/);
  assert.match(html, /Nothing was granted/); assert.doesNotMatch(html, /type="password"/);
  assert.match(html, /auth\/invitations\/accept/);
});
test("fresh browser starts at OIDC, not a dashboard password form", () => {
  const html = renderToStaticMarkup(createElement(InvitationAcceptance, { ready: false, notice: null }));
  assert.match(html, /Sign in or create an account/); assert.doesNotMatch(html, /password|type="email"|<input/);
});
test("invitation transition keeps exact scope under technical details", () => {
  const html = renderToStaticMarkup(createElement(InvitationTransition, { tenant, site, finish: true }));
  assert.match(html, /Open Home/); assert.match(html, /class="technical-details"/);
  assert.match(html, /auth\/invitations\/finish/);
  const invalid = renderToStaticMarkup(createElement(InvitationTransition, { tenant: "bad", site, finish: true }));
  assert.doesNotMatch(invalid, /<form/); assert.match(invalid, /No site was selected/);
});
for (const [name, handler] of [["verify", verify], ["accept", accept], ["join", join], ["finish", finish], ["issue", issue]] as const) {
  test(`${name} dashboard route rejects missing browser proof before I/O`, async () => {
    const response = await handler(new Request(`${origin}/auth/invitations/${name}`, { method: "POST" }));
    assert.equal(response.status, 403); assert.equal(response.headers.getSetCookie().length, 0);
  });
}

test("accepted teammate selects exact organization and site and arrives on Home", async () => {
  const original = globalThis.fetch;
  const calls: string[] = [];
  try {
    globalThis.fetch = async (url, init) => {
      const path = new URL(String(url)).pathname;
      calls.push(path);
      if (path === "/v1/session/csrf" || path === "/v1/session/tenant-csrf") return Response.json({ schema_version: 1, csrf_token: csrf });
      if (path === "/v1/session/switch-tenant") {
        assert.deepEqual(JSON.parse(String(init?.body)), { tenant_id: tenant });
        return Response.json({ schema_version: 1, tenant_id: tenant, user_id: user, role_key: "viewer", authentication_level: "primary",
          expires_at: new Date(Date.now() + 3600000).toISOString(), csrf_token: csrf }, { headers: { "Set-Cookie": cookie(TENANT_COOKIE_NAME, token, 3600) } });
      }
      if (path === "/v1/session") return Response.json({ schema_version: 2, tenant_id: tenant, user_id: user, role_key: "viewer", authentication_level: "primary",
        expires_at: new Date(Date.now() + 3600000).toISOString(), session_version: 1, active_site_id: null });
      if (path === "/v1/session/site") {
        assert.deepEqual(JSON.parse(String(init?.body)), { site_id: site, expected_session_version: 1 });
        assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
        return Response.json({ schema_version: 1, tenant_id: tenant, user_id: user, site_id: site, session_version: 2, changed: true });
      }
      throw new Error(`Unexpected route ${path}`);
    };
    const response = await finish(new Request(`${origin}/auth/invitations/finish`, { method: "POST",
      headers: { Origin: origin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded", Cookie: `${IDENTITY_COOKIE_NAME}=${token}` },
      body: new URLSearchParams({ tenant, site }) }));
    assert.equal(response.status, 303); assert.equal(response.headers.get("location"), `${origin}/`);
    assert.equal(response.headers.getSetCookie().length, 1);
    assert.deepEqual(calls, ["/v1/session/csrf", "/v1/session/switch-tenant", "/v1/session", "/v1/session/tenant-csrf", "/v1/session/site"]);
  } finally { globalThis.fetch = original; }
});

test("wrong-tenant membership selection failure sets no session and never selects a site", async () => {
  const original = globalThis.fetch;
  const calls: string[] = [];
  try {
    globalThis.fetch = async (url) => {
      const path = new URL(String(url)).pathname; calls.push(path);
      return path === "/v1/session/csrf" ? Response.json({ schema_version: 1, csrf_token: csrf }) : Response.json({}, { status: 403 });
    };
    const response = await finish(new Request(`${origin}/auth/invitations/finish`, { method: "POST",
      headers: { Origin: origin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded", Cookie: `${IDENTITY_COOKIE_NAME}=${token}` },
      body: new URLSearchParams({ tenant, site }) }));
    assert.equal(response.headers.get("location"), `${origin}/invitations/accept?notice=denied`);
    assert.equal(response.headers.getSetCookie().length, 0);
    assert.deepEqual(calls, ["/v1/session/csrf", "/v1/session/switch-tenant"]);
  } finally { globalThis.fetch = original; }
});

test("invalid invitation names keep proofs and show a name-specific error without I/O", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new Error("must not run"); };
    for (const display_name of ["   ", "n".repeat(201), "Name\u0000"]) {
      const response = await accept(new Request(`${origin}/auth/invitations/accept`, { method: "POST",
        headers: { Origin: origin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded",
          Cookie: `${INVITATION_IDENTITY_COOKIE_NAME}=${token}; ${linkCookie(credential).split(";", 1)[0]}` },
        body: new URLSearchParams({ display_name }) }));
      assert.equal(response.headers.get("location"), `${origin}/invitations/accept?notice=invalid-name`);
      assert.equal(response.headers.getSetCookie().length, 0);
    }
    const html = renderToStaticMarkup(createElement(InvitationAcceptance, { ready: true, notice: "invalid-name" }));
    assert.match(html, /Enter your name/); assert.doesNotMatch(html, /may have expired/);
  } finally { globalThis.fetch = original; }
});

test("padded invitation names are trimmed before strict acceptance and successful cookies clear", async () => {
  const original = globalThis.fetch;
  try {
    let calls = 0;
    globalThis.fetch = async (_url, init) => {
      if (++calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
      assert.equal(JSON.parse(String(init?.body)).display_name, "A teammate");
      return Response.json({ schema_version: 1, invitation_id: invitation, tenant_id: tenant, site_id: site,
        user_id: user, role_key: "viewer", accepted_at: "2026-10-04T00:00:00Z" }, { headers: { "Set-Cookie": cookie(INVITATION_IDENTITY_COOKIE_NAME, '""', 0) } });
    };
    const response = await accept(new Request(`${origin}/auth/invitations/accept`, { method: "POST",
      headers: { Origin: origin, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded",
        Cookie: `${INVITATION_IDENTITY_COOKIE_NAME}=${token}; ${linkCookie(credential).split(";", 1)[0]}` },
      body: new URLSearchParams({ display_name: "  A teammate  " }) }));
    assert.equal(calls, 2); assert.equal(response.headers.getSetCookie().length, 2);
    assert.equal(response.headers.get("location"), `${origin}/invitations/accepted?tenant=${tenant}&site=${site}`);
  } finally { globalThis.fetch = original; }
});
