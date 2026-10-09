import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { DashboardView } from "../components/dashboard-view";
import { GithubConnector } from "../components/owner-connectors";
import { SetupChecklist } from "../components/home-panels";
import { WeeklyLoopControl } from "../components/weekly-loop-control";
import { POST as weeklyPost } from "../app/actions/weekly-loop/route";
import { POST as prPost } from "../app/auth/github-pr/route";
import { githubPrState, loadOwnerConnector, type GithubState, type GithubPrState } from "../lib/owner-connectors-api";
import { loadWeeklyPause, mutateWeeklyLoop } from "../lib/weekly-loop-api";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const site = "11111111-1111-4111-8111-111111111111", extension = "22222222-2222-4222-8222-222222222222";
const token = "synthetic-" + "t".repeat(33), csrf = "synthetic-" + "c".repeat(33);
const origin = "https://dashboard.example.invalid";
const scope = { github_installation_id: 1, github_owner: "example", github_repository: "site", github_base_branch: "main", github_content_path: "app/page.tsx" };
const github: GithubState = { availability: "active", binding_id: site, installation_id: 1, repository_id: 1, owner: "example", repository: "site", base_branch: "main", base_sha: "a".repeat(40), content_path: "app/page.tsx", base_protection: "protected", failure_code: null };
const pr: GithubPrState = { availability: "observed", extension_id: extension, idempotency_key: extension, binding_id: site, owner: "example", repository: "site", base_branch: "main", content_path: "app/page.tsx" };
const paused = { state: "paused", epoch: 1, draining_observations: 1, durability: "AUTHORITY_DURABILITY_PENDING" };

function view(overrides: Record<string, unknown> = {}) {
  return renderToStaticMarkup(<DashboardView authNotice={null}
    snapshot={{ fetchedAt: "2026-10-04T10:00:00Z", connection: "connected", dependencies: "ready", inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] }}
    session={{ state: "authenticated", tenantId: site, role: "owner", authenticationLevel: "mfa", expiresAt: "2026-10-04T11:00:00Z", sessionVersion: 1, activeSiteId: site }}
    sites={{ state: "available", tenantId: site, tenantName: "Synthetic organization", sites: [{ id: site, name: "Synthetic site", primaryOrigin: "https://site.example.invalid", timezone: "UTC", reportingCurrency: "USD", state: "active", ownershipStatus: "verified" }] }}
    organizations={{ state: "absent" }} {...overrides} />);
}

test("PR scope and granted state are loaded, exact identifiers stay under Technical details", () => {
  const html = renderToStaticMarkup(<GithubConnector state={github} prState={pr} siteId={site} owner />);
  assert.match(html, /Let Signal open pull requests/);
  assert.match(html, />Granted</);
  assert.match(html, /<details class="technical-details"><summary>Technical details<\/summary>[^]*example\/site[^]*main[^]*app\/page.tsx[^]*22222222[^]*<\/details>/);
  assert.match(html, />Revoke<\/button>/);
  assert.match(html, /never allows merging, pushing to the default branch, editing CI workflows or reading secrets/);
  assert.match(html, /signal\/&lt;32-character operation ID&gt;/);
  const unknown = renderToStaticMarkup(<GithubConnector state={github} prState={{ availability: "unavailable" }} siteId={site} owner />);
  assert.match(unknown, /Could not check/);
  assert.doesNotMatch(unknown, /class="primary-command"|>Granted</);
  const pending = renderToStaticMarkup(<GithubConnector state={github} prState={{ ...pr, availability: "prepared" }} siteId={site} owner />);
  assert.match(pending, /Finish granting permission/);
  const viewer = renderToStaticMarkup(<GithubConnector state={github} prState={pr} siteId={site} owner={false} />);
  assert.doesNotMatch(viewer, /Revoke|Let Signal open pull requests/);
});

test("optional PR checklist never treats an unread state as done", () => {
  const checklist = (state: GithubPrState) => renderToStaticMarkup(<SetupChecklist verified={false} gsc={{ availability: "unbound" }} github={github} githubPr={state} slack={{ availability: "unavailable" }} telegram={{ availability: "unavailable" }} standing={{ state: "unavailable" }} />);
  assert.match(checklist({ availability: "unavailable" }), /data-state="unknown"[^]*Let Signal open pull requests[^]*Could not check/);
  assert.match(checklist(pr), /data-state="done"[^]*Let Signal open pull requests[^]*Optional[^]*Done/);
  assert.match(checklist({ availability: "ungranted" }), /data-state="todo"[^]*Let Signal open pull requests/);
});

test("owners reach visibility schedule, weekly controls on Home and Autonomy, and honest empty pages", () => {
  const visibility = view({ activeSection: "visibility" });
  assert.match(visibility, /id="visibility-schedule-title"/);
  const viewer = view({ activeSection: "visibility", session: { state: "authenticated", tenantId: site, role: "viewer", authenticationLevel: "primary", expiresAt: "2026-10-04T11:00:00Z", sessionVersion: 1, activeSiteId: site } });
  assert.doesNotMatch(viewer, /visibility-schedule-title/);
  for (const activeSection of ["overview", "policy"]) {
    assert.match(view({ activeSection, weeklyPause: { state: "available", paused: false } }), /Pause weekly loop/);
    assert.match(view({ activeSection, weeklyPause: { state: "available", paused: true } }), /Resume weekly loop/);
  }
  const unknown = renderToStaticMarkup(<WeeklyLoopControl siteId={site} state={{ state: "unavailable" }} />);
  assert.match(unknown, /Could not check/); assert.doesNotMatch(unknown, /<button/);
  const activity = view({ activeSection: "changes", github, githubPr: pr });
  assert.match(activity, /No pull request evidence shown/);
  assert.doesNotMatch(activity, /GitHub pull requests[^]*PR-only connector[^]*Not connected/);
  const inbox = view({ activeSection: "approvals" });
  assert.match(inbox, /Nothing waits on you\.[^]*Signal adds decisions here as the weekly loop finds work\./);
  assert.doesNotMatch(inbox, /prepare its proposal in Ask Signal/);
  assert.match(view({ activeSection: "approvals", localPilot: true }), /prepare its proposal in Ask Signal/);
});

test("PR projection rejects cross-resource state, secrets and malformed state", async () => {
  assert.deepEqual(githubPrState(pr, scope), pr);
  for (const bad of [{ ...pr, repository: "other" }, { ...pr, extension_id: "bad" }, { ...pr, private_key: "synthetic" }, { ...pr, availability: "granted" }]) assert.deepEqual(githubPrState(bad, scope), { availability: "unavailable" });
  for (const response of [Response.json({}, { status: 503 }), Response.json(pr, { headers: { "Set-Cookie": "synthetic" } })]) {
    assert.deepEqual(await loadOwnerConnector({ connector: "github-pr", siteId: site, tenantToken: token, configuration: { github: scope, property: origin + "/" }, fetcher: async () => response }), { availability: "unavailable" });
  }
});

test("weekly pause reads and mutations validate exact scope, CSRF and pending durability", async () => {
  const input = { tenantToken: token, siteId: site };
  assert.deepEqual(await loadWeeklyPause({ ...input, fetcher: async () => Response.json({ site_id: site, paused: true }) }), { state: "available", paused: true });
  for (const value of [{ site_id: extension, paused: true }, { site_id: site, paused: "true" }, { site_id: site, paused: true, secret: "synthetic" }]) assert.deepEqual(await loadWeeklyPause({ ...input, fetcher: async () => Response.json(value) }), { state: "unavailable" });
  let calls = 0;
  const result = await mutateWeeklyLoop({ ...input, origin, operation: "pause", fetcher: async (url, init) => {
    calls++;
    if (calls === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
    assert.match(String(url), new RegExp(`/v1/sites/${site}/weekly-loop/pause$`));
    assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrf);
    assert.equal(init?.redirect, "error");
    return Response.json(paused);
  } });
  assert.deepEqual(result, paused); assert.equal(calls, 2);
  assert.equal(await mutateWeeklyLoop({ ...input, origin, operation: "pause", fetcher: async () => { throw new Error("unknown outcome"); } }), null);
});

test("weekly and PR BFF mutations reject forged browser proof and relay only reviewed operations", async () => {
  const oldOrigin = process.env.SIGNAL_DASHBOARD_ORIGIN, oldApi = process.env.SIGNAL_API_BASE_URL, fetcher = globalThis.fetch;
  process.env.SIGNAL_DASHBOARD_ORIGIN = origin;
  process.env.SIGNAL_API_BASE_URL = "http://127.0.0.1:8000";
  let calls = 0;
  globalThis.fetch = async (_url, init) => {
    calls++;
    if (calls % 2 === 1) return Response.json({ schema_version: 1, csrf_token: csrf });
    return Response.json(String(init?.body) === "{}" ? paused : { state: "revoked", durability: "AUTHORITY_DURABILITY_PENDING" });
  };
  const headers = { "Content-Type": "application/json", Origin: origin, "Sec-Fetch-Site": "same-origin", Cookie: `${TENANT_COOKIE_NAME}=${token}` };
  try {
    for (const [path, post, body] of [["/actions/weekly-loop", weeklyPost, { site_id: site, operation: "pause" }], ["/auth/github-pr", prPost, { site_id: site, operation: "revoke", extension_id: extension }]] as const) {
      for (const changed of [{ Origin: "https://evil.invalid" }, { Cookie: "" }, { "Sec-Fetch-Site": "cross-site" }]) {
        const before = calls;
        assert.equal((await post(new Request(origin + path, { method: "POST", headers: { ...headers, ...changed }, body: JSON.stringify(body) }))).status, 403);
        assert.equal(calls, before);
      }
      const response = await post(new Request(origin + path, { method: "POST", headers, body: JSON.stringify(body) }));
      assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal((await response.json()).durability, "AUTHORITY_DURABILITY_PENDING");
      assert.equal((await post(new Request(origin + path, { method: "POST", headers, body: JSON.stringify({ ...body, operation: "merge" }) }))).status, 403);
    }
  } finally {
    globalThis.fetch = fetcher;
    if (oldOrigin === undefined) delete process.env.SIGNAL_DASHBOARD_ORIGIN; else process.env.SIGNAL_DASHBOARD_ORIGIN = oldOrigin;
    if (oldApi === undefined) delete process.env.SIGNAL_API_BASE_URL; else process.env.SIGNAL_API_BASE_URL = oldApi;
  }
});
