import type { AuthRelayResult } from "./browser-auth";

export type AuthNoticeKey =
  | "browser-cleared"
  | "callback-failed"
  | "callback-rejected"
  | "identity-not-ready"
  | "identity-ready"
  | "logged-out"
  | "logout-failed"
  | "logout-rejected"
  | "organization-failed"
  | "organization-rejected"
  | "request-rejected"
  | "site-conflict"
  | "site-create-conflict"
  | "site-create-failed"
  | "site-create-rejected"
  | "site-created"
  | "site-failed"
  | "site-rejected"
  | "site-selected"
  | "signed-in";

export function authRelayResponse(
  dashboardOrigin: string,
  result: AuthRelayResult,
  errors: {
    not_ready: AuthNoticeKey;
    rejected: AuthNoticeKey;
    conflict?: AuthNoticeKey;
    failed: AuthNoticeKey;
  },
): Response {
  const errorNotice =
    result.state === "conflict"
      ? (errors.conflict ?? errors.rejected)
      : result.state === "redirect"
        ? null
        : errors[result.state];
  let destination =
    result.state === "redirect" ? result.location : `/?auth=${errorNotice}`;
  let cookies = result.cookies;
  try {
    destination = relayLocation(destination, dashboardOrigin);
  } catch {
    destination = new URL(`/?auth=${errors.failed}`, dashboardOrigin).href;
    cookies = [];
  }
  const response = new Response(null, {
    status: 303,
    headers: {
      "Cache-Control": "no-store",
      Location: destination,
    },
  });
  for (const cookie of cookies) response.headers.append("Set-Cookie", cookie);
  return response;
}

export function rejectedMutationResponse(): Response {
  return Response.json(
    {
      error: {
        code: "BROWSER_REQUEST_REJECTED",
        message: "The browser request could not be authorized.",
      },
    },
    {
      status: 403,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

export function localNoticeResponse(
  dashboardOrigin: string,
  notice: AuthNoticeKey,
  cookies: readonly string[] = [],
): Response {
  const response = new Response(null, {
    status: 303,
    headers: {
      "Cache-Control": "no-store",
      Location: new URL(`/?auth=${notice}`, dashboardOrigin).href,
    },
  });
  for (const cookie of cookies) response.headers.append("Set-Cookie", cookie);
  return response;
}

export function identityServiceUnavailableResponse(): Response {
  return Response.json(
    {
      error: {
        code: "DASHBOARD_IDENTITY_NOT_READY",
        message: "The dashboard identity boundary is not configured.",
      },
    },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

function relayLocation(destination: string, dashboardOrigin: string): string {
  if (destination.startsWith("/") && !destination.startsWith("//")) {
    return new URL(destination, dashboardOrigin).href;
  }
  const external = new URL(destination);
  const dashboard = new URL(dashboardOrigin);
  const localPilotHttp =
    dashboard.protocol === "http:" &&
    isLoopbackHost(dashboard.hostname) &&
    external.protocol === "http:" &&
    isLoopbackHost(external.hostname);
  if (
    (external.protocol !== "https:" && !localPilotHttp) ||
    external.username !== "" ||
    external.password !== "" ||
    external.hash !== ""
  ) {
    throw new Error("Unsafe relay location");
  }
  return external.href;
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}
