import assert from "node:assert/strict";
import test from "node:test";

import {
  IDENTITY_COOKIE_NAME,
  INVITATION_IDENTITY_COOKIE_NAME,
  OIDC_BINDING_COOKIE_NAME,
  TENANT_COOKIE_NAME,
  beginSignalLogin,
  browserCookieConfiguration,
  clearBrowserAuthCookies,
  completeSignalLogin,
  dashboardMutationAccepted,
  exactCookie,
  logoutSignalSession,
  onboardSignalSite,
  selectSignalOrganization,
  selectSignalSite,
  validatedDashboardOrigin,
} from "../lib/browser-auth";

const token = "a".repeat(43);
const secondToken = "b".repeat(43);
const csrfToken = "c".repeat(43);
const tenantId = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const siteId = "33333333-3333-4333-8333-333333333333";

test("local cookie transport cannot weaken production browser authority", () => {
  assert.deepEqual(browserCookieConfiguration("development", "1"), {
    identity: "signal_local_identity",
    tenant: "signal_local_session",
    oidcBinding: "signal_local_oidc_binding",
    invitationIdentity: "signal_local_invitation_identity",
    secure: false,
  });
  assert.deepEqual(browserCookieConfiguration("development", "1", "a1b2c3d4"), {
    identity: "signal_local_a1b2c3d4_identity",
    tenant: "signal_local_a1b2c3d4_session",
    oidcBinding: "signal_local_a1b2c3d4_oidc_binding",
    invitationIdentity: "signal_local_a1b2c3d4_invitation_identity",
    secure: false,
  });
  assert.deepEqual(browserCookieConfiguration("development", "1", "invalid"), {
    identity: "signal_local_identity",
    tenant: "signal_local_session",
    oidcBinding: "signal_local_oidc_binding",
    invitationIdentity: "signal_local_invitation_identity",
    secure: false,
  });
  assert.equal(browserCookieConfiguration("production", "1").secure, true);
  assert.equal(browserCookieConfiguration("development", "0").secure, true);
  assert.match(browserCookieConfiguration("production", "1").identity, /^__Host-/);
});

function issuedCookie(name: string, value = token, maxAge = 600): string {
  return `${name}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

function clearedCookie(name: string): string {
  return `${name}=""; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;
}

function responseWithCookies(
  status: number,
  cookies: readonly string[],
  headers: Record<string, string> = {},
  body: unknown = null,
): Response {
  const responseHeaders = new Headers(headers);
  if (body !== null) responseHeaders.set("Content-Type", "application/json");
  for (const cookie of cookies) responseHeaders.append("Set-Cookie", cookie);
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: responseHeaders,
  });
}

function regularLoginCookies(): string[] {
  return [
    issuedCookie(IDENTITY_COOKIE_NAME, token, 900),
    clearedCookie(TENANT_COOKIE_NAME),
    clearedCookie(OIDC_BINDING_COOKIE_NAME),
    clearedCookie(INVITATION_IDENTITY_COOKIE_NAME),
  ];
}

test("validates dashboard origins and exact same-origin mutation proof", () => {
  assert.equal(validatedDashboardOrigin(undefined, "development"), "http://localhost:3000");
  assert.equal(
    validatedDashboardOrigin("https://dashboard.signal.test", "production"),
    "https://dashboard.signal.test",
  );
  assert.throws(() => validatedDashboardOrigin(undefined, "production"));
  assert.throws(() => validatedDashboardOrigin("http://dashboard.signal.test", "development"));
  assert.throws(() => validatedDashboardOrigin("https://dashboard.signal.test/path", "production"));

  assert.equal(
    dashboardMutationAccepted(
      new Request("http://localhost:3000/auth/login", {
        method: "POST",
        headers: { Origin: "http://localhost:3000", "Sec-Fetch-Site": "same-origin" },
      }),
      "http://localhost:3000",
    ),
    true,
  );
  assert.equal(
    dashboardMutationAccepted(
      new Request("http://127.0.0.1:3000/auth/login", {
        method: "POST",
        headers: { Origin: "null", "Sec-Fetch-Site": "same-origin" },
      }),
      "http://127.0.0.1:3000",
    ),
    true,
  );
  for (const request of [
    new Request("http://localhost:3000/auth/login", {
      method: "GET",
      headers: { Origin: "http://localhost:3000", "Sec-Fetch-Site": "same-origin" },
    }),
    new Request("http://localhost:3000/auth/login", {
      method: "POST",
      headers: { Origin: "https://attacker.test", "Sec-Fetch-Site": "same-origin" },
    }),
    new Request("http://localhost:3000/auth/login", {
      method: "POST",
      headers: { Origin: "http://localhost:3000", "Sec-Fetch-Site": "cross-site" },
    }),
  ]) {
    assert.equal(dashboardMutationAccepted(request, "http://localhost:3000"), false);
  }
  assert.equal(
    dashboardMutationAccepted(
      new Request("https://dashboard.signal.test/auth/login", {
        method: "POST",
        headers: { Origin: "null", "Sec-Fetch-Site": "same-origin" },
      }),
      "https://dashboard.signal.test",
    ),
    false,
  );
});

test("extracts only one exact opaque authority cookie", () => {
  assert.equal(exactCookie(`x=1; ${IDENTITY_COOKIE_NAME}=${token}`, IDENTITY_COOKIE_NAME), token);
  assert.equal(exactCookie(`${IDENTITY_COOKIE_NAME}=short`, IDENTITY_COOKIE_NAME), null);
  assert.equal(
    exactCookie(
      `${IDENTITY_COOKIE_NAME}=${token}; ${IDENTITY_COOKIE_NAME}=${secondToken}`,
      IDENTITY_COOKIE_NAME,
    ),
    null,
  );
  assert.equal(exactCookie(null, IDENTITY_COOKIE_NAME), null);
});

test("starts login only for the configured provider and exact cookie contract", async () => {
  const result = await beginSignalLogin({
    baseUrl: "https://api.signal.test",
    identityProviderOrigin: "https://identity.signal.test",
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      assert.equal(url.origin + url.pathname, "https://api.signal.test/v1/session/login");
      assert.equal(url.searchParams.get("return_path"), "/?auth=identity-ready");
      assert.equal(init?.redirect, "manual");
      return responseWithCookies(
        303,
        [
          clearedCookie(IDENTITY_COOKIE_NAME),
          clearedCookie(TENANT_COOKIE_NAME),
          issuedCookie(OIDC_BINDING_COOKIE_NAME),
          clearedCookie(INVITATION_IDENTITY_COOKIE_NAME),
        ],
        { Location: "https://identity.signal.test/authorize?state=opaque" },
      );
    },
  });

  assert.equal(result.state, "redirect");
  assert.equal(
    result.state === "redirect" ? result.location : "",
    "https://identity.signal.test/authorize?state=opaque",
  );
  assert.equal(result.cookies.length, 4);
});

test("permits loopback HTTP identity only outside production", async () => {
  const fetcher = async () =>
    responseWithCookies(
      303,
      [
        clearedCookie(IDENTITY_COOKIE_NAME),
        clearedCookie(TENANT_COOKIE_NAME),
        issuedCookie(OIDC_BINDING_COOKIE_NAME),
        clearedCookie(INVITATION_IDENTITY_COOKIE_NAME),
      ],
      { Location: "http://127.0.0.1:18080/authorize?state=opaque" },
    );

  assert.equal(
    (
      await beginSignalLogin({
        baseUrl: "http://127.0.0.1:8000",
        identityProviderOrigin: "http://127.0.0.1:18080",
        environment: "development",
        fetcher,
      })
    ).state,
    "redirect",
  );
  assert.equal(
    (
      await beginSignalLogin({
        baseUrl: "http://127.0.0.1:8000",
        identityProviderOrigin: "http://127.0.0.1:18080",
        environment: "production",
        fetcher: async () => assert.fail("Production must reject before I/O"),
      })
    ).state,
    "not_ready",
  );
});

test("rejects login redirects, unexpected cookies, and missing configuration", async () => {
  let dispatched = false;
  const unavailable = await beginSignalLogin({
    baseUrl: "https://api.signal.test",
    fetcher: async () => {
      dispatched = true;
      return new Response();
    },
  });
  assert.equal(dispatched, false);
  assert.equal(unavailable.state, "not_ready");

  for (const response of [
    responseWithCookies(303, regularLoginCookies(), {
      Location: "https://attacker.test/authorize",
    }),
    responseWithCookies(
      303,
      [
        clearedCookie(IDENTITY_COOKIE_NAME),
        clearedCookie(TENANT_COOKIE_NAME),
        issuedCookie(OIDC_BINDING_COOKIE_NAME),
        issuedCookie(INVITATION_IDENTITY_COOKIE_NAME),
      ],
      { Location: "https://identity.signal.test/authorize" },
    ),
  ]) {
    const result = await beginSignalLogin({
      baseUrl: "https://api.signal.test",
      identityProviderOrigin: "https://identity.signal.test",
      fetcher: async () => response,
    });
    assert.deepEqual(result, { state: "failed", cookies: [] });
  }
});

test("completes login with only the browser binding and a fixed local return", async () => {
  const result = await completeSignalLogin({
    baseUrl: "https://api.signal.test",
    state: token,
    code: "provider-code",
    browserBinding: secondToken,
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      assert.equal(url.pathname, "/v1/session/callback");
      assert.equal(url.searchParams.get("state"), token);
      assert.equal(url.searchParams.get("code"), "provider-code");
      assert.equal(
        new Headers(init?.headers).get("cookie"),
        `${OIDC_BINDING_COOKIE_NAME}=${secondToken}`,
      );
      return responseWithCookies(303, regularLoginCookies(), {
        Location: "/?auth=identity-ready",
      });
    },
  });

  assert.equal(result.state, "redirect");
  assert.equal(result.state === "redirect" ? result.location : "", "/?auth=identity-ready");
});

test("rejects malformed callback input without dispatch and clears only the binding", async () => {
  let dispatched = false;
  const result = await completeSignalLogin({
    state: "bad",
    code: "provider-code",
    browserBinding: token,
    fetcher: async () => {
      dispatched = true;
      return new Response();
    },
  });
  assert.equal(dispatched, false);
  assert.deepEqual(result, {
    state: "rejected",
    cookies: [clearedCookie(OIDC_BINDING_COOKIE_NAME)],
  });
});

test("relays only validated cookie deletions from API error responses", async () => {
  const cleared = await completeSignalLogin({
    baseUrl: "https://api.signal.test",
    state: token,
    code: "provider-code",
    browserBinding: secondToken,
    fetcher: async () =>
      responseWithCookies(401, [clearedCookie(OIDC_BINDING_COOKIE_NAME)]),
  });
  assert.deepEqual(cleared, {
    state: "rejected",
    cookies: [clearedCookie(OIDC_BINDING_COOKIE_NAME)],
  });

  const unsafe = await completeSignalLogin({
    baseUrl: "https://api.signal.test",
    state: token,
    code: "provider-code",
    browserBinding: secondToken,
    fetcher: async () => responseWithCookies(401, [issuedCookie(IDENTITY_COOKIE_NAME)]),
  });
  assert.deepEqual(unsafe, { state: "failed", cookies: [] });

  const futureExpiry = await completeSignalLogin({
    baseUrl: "https://api.signal.test",
    state: token,
    code: "provider-code",
    browserBinding: secondToken,
    fetcher: async () =>
      responseWithCookies(401, [
        `${OIDC_BINDING_COOKIE_NAME}=""; Path=/; Max-Age=0; ` +
          "Expires=Thu, 01 Jan 2099 00:00:00 GMT; HttpOnly; Secure; SameSite=Lax",
      ]),
  });
  assert.deepEqual(futureExpiry, { state: "failed", cookies: [] });
});

test("selects an organization through CSRF-protected API calls", async () => {
  const calls: string[] = [];
  const result = await selectSignalOrganization({
    baseUrl: "https://api.signal.test",
    identityToken: token,
    tenantId,
    dashboardOrigin: "https://dashboard.signal.test",
    now: () => new Date("2026-09-09T20:00:00.000Z"),
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      assert.equal(
        new Headers(init?.headers).get("cookie"),
        `${IDENTITY_COOKIE_NAME}=${token}`,
      );
      if (url.pathname === "/v1/session/csrf") {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      assert.equal(init?.method, "POST");
      assert.equal(new Headers(init?.headers).get("origin"), "https://dashboard.signal.test");
      assert.equal(new Headers(init?.headers).get("x-csrf-token"), csrfToken);
      assert.equal(init?.body, JSON.stringify({ tenant_id: tenantId }));
      const headers = new Headers({ "Content-Type": "application/json" });
      headers.append("Set-Cookie", issuedCookie(TENANT_COOKIE_NAME, secondToken, 900));
      return new Response(
        JSON.stringify({
          schema_version: 1,
          tenant_id: tenantId,
          user_id: userId,
          role_key: "owner",
          authentication_level: "mfa",
          csrf_token: csrfToken,
          expires_at: "2026-09-09T20:15:00.000Z",
        }),
        { status: 200, headers },
      );
    },
  });

  assert.deepEqual(calls, ["/v1/session/csrf", "/v1/session/switch-tenant"]);
  assert.deepEqual(result, {
    state: "redirect",
    location: "/?auth=signed-in",
    cookies: [issuedCookie(TENANT_COOKIE_NAME, secondToken, 900)],
  });
});

test("does not select an organization after malformed CSRF or tenant responses", async () => {
  let calls = 0;
  const malformedCsrf = await selectSignalOrganization({
    identityToken: token,
    tenantId,
    dashboardOrigin: "http://localhost:3000",
    fetcher: async () => {
      calls += 1;
      return Response.json({ schema_version: 1, csrf_token: "short" });
    },
  });
  assert.equal(calls, 1);
  assert.deepEqual(malformedCsrf, { state: "failed", cookies: [] });

  const malformedSession = await selectSignalOrganization({
    identityToken: token,
    tenantId,
    dashboardOrigin: "http://localhost:3000",
    now: () => new Date("2026-09-09T20:00:00.000Z"),
    fetcher: async (input) =>
      new URL(String(input)).pathname.endsWith("/csrf")
        ? Response.json({ schema_version: 1, csrf_token: csrfToken })
        : Response.json({ unexpected: true }),
  });
  assert.deepEqual(malformedSession, { state: "failed", cookies: [] });
});

test("selects one site through the exact tenant session and CSRF boundary", async () => {
  const calls: string[] = [];
  const result = await selectSignalSite({
    baseUrl: "https://api.signal.test",
    tenantToken: token,
    siteId,
    expectedSessionVersion: 4,
    dashboardOrigin: "https://dashboard.signal.test",
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("cookie"), `${TENANT_COOKIE_NAME}=${token}`);
      if (url.pathname === "/v1/session/tenant-csrf") {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      assert.equal(init?.method, "PUT");
      assert.equal(headers.get("origin"), "https://dashboard.signal.test");
      assert.equal(headers.get("sec-fetch-site"), "same-origin");
      assert.equal(headers.get("x-csrf-token"), csrfToken);
      assert.equal(
        init?.body,
        JSON.stringify({ site_id: siteId, expected_session_version: 4 }),
      );
      return Response.json({
        schema_version: 1,
        tenant_id: tenantId,
        user_id: userId,
        site_id: siteId,
        session_version: 5,
        changed: true,
      });
    },
  });

  assert.deepEqual(calls, ["/v1/session/tenant-csrf", "/v1/session/site"]);
  assert.deepEqual(result, {
    state: "redirect",
    location: "/?auth=site-selected",
    cookies: [],
  });
});

test("fails closed for malformed site selection input and authority responses", async () => {
  let dispatched = false;
  for (const input of [
    { tenantToken: "short", siteId, expectedSessionVersion: 1 },
    { tenantToken: token, siteId: "not-a-uuid", expectedSessionVersion: 1 },
    { tenantToken: token, siteId, expectedSessionVersion: 0 },
    { tenantToken: token, siteId, expectedSessionVersion: 1.5 },
  ]) {
    const result = await selectSignalSite({
      ...input,
      dashboardOrigin: "http://localhost:3000",
      fetcher: async () => {
        dispatched = true;
        return new Response();
      },
    });
    assert.deepEqual(result, { state: "rejected", cookies: [] });
  }
  assert.equal(dispatched, false);

  const malformed = await selectSignalSite({
    tenantToken: token,
    siteId,
    expectedSessionVersion: 4,
    dashboardOrigin: "http://localhost:3000",
    fetcher: async (input) =>
      new URL(String(input)).pathname.endsWith("tenant-csrf")
        ? Response.json({ schema_version: 1, csrf_token: csrfToken })
        : Response.json({
            schema_version: 1,
            tenant_id: tenantId,
            user_id: userId,
            site_id: siteId,
            session_version: 4,
            changed: true,
          }),
  });
  assert.deepEqual(malformed, { state: "failed", cookies: [] });

  const unsafeCookie = await selectSignalSite({
    tenantToken: token,
    siteId,
    expectedSessionVersion: 4,
    dashboardOrigin: "http://localhost:3000",
    fetcher: async (input) => {
      if (new URL(String(input)).pathname.endsWith("tenant-csrf")) {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      const response = Response.json({
        schema_version: 1,
        tenant_id: tenantId,
        user_id: userId,
        site_id: siteId,
        session_version: 5,
        changed: true,
      });
      response.headers.append("Set-Cookie", issuedCookie(TENANT_COOKIE_NAME));
      return response;
    },
  });
  assert.deepEqual(unsafeCookie, { state: "failed", cookies: [] });
});

test("preserves closed conflict and rejected site-selection outcomes", async () => {
  for (const [status, state] of [
    [409, "conflict"],
    [403, "rejected"],
    [503, "not_ready"],
  ] as const) {
    const result = await selectSignalSite({
      tenantToken: token,
      siteId,
      expectedSessionVersion: 4,
      dashboardOrigin: "http://localhost:3000",
      fetcher: async (input) =>
        new URL(String(input)).pathname.endsWith("tenant-csrf")
          ? Response.json({ schema_version: 1, csrf_token: csrfToken })
          : new Response(null, { status }),
    });
    assert.deepEqual(result, { state, cookies: [] });
  }
});

test("onboards one site through the exact tenant session and CSRF boundary", async () => {
  const calls: string[] = [];
  const requestId = "44444444-4444-4444-8444-444444444444";
  const result = await onboardSignalSite({
    baseUrl: "https://api.signal.test",
    tenantToken: token,
    idempotencyKey: requestId,
    name: "Acme Store",
    primaryOrigin: "https://www.example.com",
    timezone: "America/Phoenix",
    reportingCurrency: "USD",
    expectedSessionVersion: 7,
    dashboardOrigin: "https://dashboard.signal.test",
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      const headers = new Headers(init?.headers);
      assert.equal(headers.get("cookie"), `${TENANT_COOKIE_NAME}=${token}`);
      if (url.pathname === "/v1/session/tenant-csrf") {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      assert.equal(init?.method, "POST");
      assert.equal(headers.get("origin"), "https://dashboard.signal.test");
      assert.equal(headers.get("sec-fetch-site"), "same-origin");
      assert.equal(headers.get("x-csrf-token"), csrfToken);
      assert.equal(
        init?.body,
        JSON.stringify({
          idempotency_key: requestId,
          name: "Acme Store",
          primary_origin: "https://www.example.com",
          timezone: "America/Phoenix",
          reporting_currency: "USD",
          expected_session_version: 7,
        }),
      );
      return Response.json({
        schema_version: 1,
        tenant_id: tenantId,
        user_id: userId,
        site_id: siteId,
        name: "Acme Store",
        primary_origin: "https://www.example.com",
        timezone: "America/Phoenix",
        reporting_currency: "USD",
        state: "onboarding",
        ownership_status: "unverified",
        session_version: 8,
        replayed: false,
      });
    },
  });

  assert.deepEqual(calls, ["/v1/session/tenant-csrf", "/v1/sites"]);
  assert.deepEqual(result, {
    state: "redirect",
    location: "/?auth=site-created",
    cookies: [],
  });
});

test("fails closed for malformed site onboarding input and authority responses", async () => {
  let dispatched = false;
  const valid = {
    tenantToken: token,
    idempotencyKey: "44444444-4444-4444-8444-444444444444",
    name: "Acme Store",
    primaryOrigin: "https://www.example.com",
    timezone: "UTC",
    reportingCurrency: "USD",
    expectedSessionVersion: 7,
    dashboardOrigin: "http://localhost:3000",
  };
  for (const input of [
    { ...valid, tenantToken: "short" },
    { ...valid, idempotencyKey: "11111111-1111-1111-8111-111111111111" },
    { ...valid, name: " Acme Store " },
    { ...valid, primaryOrigin: "http://www.example.com" },
    { ...valid, primaryOrigin: "https://127.0.0.1" },
    { ...valid, timezone: "bad time zone" },
    { ...valid, reportingCurrency: "usd" },
    { ...valid, expectedSessionVersion: 0 },
  ]) {
    const result = await onboardSignalSite({
      ...input,
      fetcher: async () => {
        dispatched = true;
        return new Response();
      },
    });
    assert.deepEqual(result, { state: "rejected", cookies: [] });
  }
  assert.equal(dispatched, false);

  const malformed = await onboardSignalSite({
    ...valid,
    fetcher: async (input) =>
      new URL(String(input)).pathname.endsWith("tenant-csrf")
        ? Response.json({ schema_version: 1, csrf_token: csrfToken })
        : Response.json({ unexpected: true }),
  });
  assert.deepEqual(malformed, { state: "failed", cookies: [] });

  const unsafeCookie = await onboardSignalSite({
    ...valid,
    fetcher: async (input) => {
      if (new URL(String(input)).pathname.endsWith("tenant-csrf")) {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      const response = Response.json({
        schema_version: 1,
        tenant_id: tenantId,
        user_id: userId,
        site_id: siteId,
        name: valid.name,
        primary_origin: valid.primaryOrigin,
        timezone: valid.timezone,
        reporting_currency: valid.reportingCurrency,
        state: "onboarding",
        ownership_status: "unverified",
        session_version: 8,
        replayed: false,
      });
      response.headers.append("Set-Cookie", issuedCookie(TENANT_COOKIE_NAME));
      return response;
    },
  });
  assert.deepEqual(unsafeCookie, { state: "failed", cookies: [] });
});

test("preserves closed site-onboarding provider outcomes", async () => {
  for (const [status, state] of [
    [409, "conflict"],
    [403, "rejected"],
    [503, "not_ready"],
  ] as const) {
    const result = await onboardSignalSite({
      tenantToken: token,
      idempotencyKey: "44444444-4444-4444-8444-444444444444",
      name: "Acme Store",
      primaryOrigin: "https://www.example.com",
      timezone: "UTC",
      reportingCurrency: "USD",
      expectedSessionVersion: 7,
      dashboardOrigin: "http://localhost:3000",
      fetcher: async (input) =>
        new URL(String(input)).pathname.endsWith("tenant-csrf")
          ? Response.json({ schema_version: 1, csrf_token: csrfToken })
          : new Response(null, { status }),
    });
    assert.deepEqual(result, { state, cookies: [] });
  }
});

test("logs out with tenant precedence and requires all authority cookies to clear", async () => {
  const calls: string[] = [];
  const result = await logoutSignalSession({
    baseUrl: "https://api.signal.test",
    cookieHeader: `${IDENTITY_COOKIE_NAME}=${token}; ${TENANT_COOKIE_NAME}=${secondToken}`,
    dashboardOrigin: "https://dashboard.signal.test",
    fetcher: async (input, init) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      const headers = new Headers(init?.headers);
      assert.equal(
        headers.get("cookie"),
        `${TENANT_COOKIE_NAME}=${secondToken}; ${IDENTITY_COOKIE_NAME}=${token}`,
      );
      if (url.pathname.endsWith("logout-csrf")) {
        return Response.json({ schema_version: 1, csrf_token: csrfToken });
      }
      assert.equal(headers.get("x-csrf-token"), csrfToken);
      return responseWithCookies(
        202,
        clearBrowserAuthCookies(),
        {},
        { schema_version: 1, status: "AUTHORITY_DURABILITY_PENDING" },
      );
    },
  });
  assert.deepEqual(calls, ["/v1/session/logout-csrf", "/v1/session/logout"]);
  assert.deepEqual(result, {
    state: "redirect",
    location: "/?auth=logged-out",
    cookies: clearBrowserAuthCookies(),
  });

  const incomplete = await logoutSignalSession({
    baseUrl: "https://api.signal.test",
    cookieHeader: `${IDENTITY_COOKIE_NAME}=${token}`,
    dashboardOrigin: "https://dashboard.signal.test",
    fetcher: async (input) =>
      new URL(String(input)).pathname.endsWith("logout-csrf")
        ? Response.json({ schema_version: 1, csrf_token: csrfToken })
        : responseWithCookies(
            202,
            [clearedCookie(IDENTITY_COOKIE_NAME)],
            {},
            { schema_version: 1, status: "AUTHORITY_DURABILITY_PENDING" },
          ),
  });
  assert.deepEqual(incomplete, { state: "failed", cookies: [] });
});
