import { INVITATION_IDENTITY_COOKIE_NAME, browserCookieConfiguration, type AuthRelayResult } from "./browser-auth";

export const INVITATION_LINK_COOKIE = `${INVITATION_IDENTITY_COOKIE_NAME}_link`;
const CREDENTIAL = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.[A-Za-z0-9_-]{43}$/;

export function invitationCredential(value: unknown): string | null {
  return typeof value === "string" && CREDENTIAL.test(value) ? value : null;
}
export function linkCookie(value: string | null): string {
  if (value !== null && invitationCredential(value) === null) throw new Error();
  return `${INVITATION_LINK_COOKIE}=${value ?? '""'}; Path=/; Max-Age=${value === null ? 0 : 600}; HttpOnly${browserCookieConfiguration().secure ? "; Secure" : ""}; SameSite=Lax`;
}
export function readLinkCookie(header: string | null): string | null {
  if (header === null || header.length > 16 * 1024) return null;
  const values = header.split(";").map(v => v.trim()).filter(v => v.startsWith(`${INVITATION_LINK_COOKIE}=`));
  return values.length === 1 ? invitationCredential(values[0].slice(INVITATION_LINK_COOKIE.length + 1)) : null;
}
export function invitationResponse(origin: string, result: AuthRelayResult, extraCookies: string[] = []): Response {
  const location = result.state === "redirect" ? result.location :
    `/invitations/accept?notice=${result.state === "rejected" ? "denied" : "unconfirmed"}`;
  const response = new Response(null, { status: 303, headers: { "Cache-Control": "no-store", Location: new URL(location, origin).href } });
  for (const cookie of [...result.cookies, ...extraCookies]) response.headers.append("Set-Cookie", cookie);
  return response;
}
