import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { boundedEmailText } from "./email-api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export const INVITE_ROLES = ["viewer", "analyst", "editor", "approver", "admin"] as const;
export type TeamState = { state: "unavailable" } | {
  state: "available"; site_id: string; truncated: boolean; can_revoke: boolean;
  invitations: { id: string; email: string; role_key: string; state: string; created_at: string; expires_at: string;
    revocation_durability: "pending" | "acknowledged" | null }[];
  members: { user_id: string; display_name: string; role_key: string }[];
};

function url(path: string): URL {
  return new URL(path, validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000"));
}
function request(cookie: string): RequestInit {
  return { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    headers: { Accept: "application/json", Cookie: cookie } };
}
async function json(response: Response): Promise<Record<string, unknown>> {
  if (response.headers.has("set-cookie") || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") throw new Error();
  const value: unknown = JSON.parse(await boundedEmailText(response.body, 128 * 1024));
  if (!record(value)) throw new Error();
  return value;
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function fields(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).sort().join(",") === keys.sort().join(",");
}
function id(value: unknown): value is string { return typeof value === "string" && UUID.test(value); }
function date(value: unknown): boolean { return typeof value === "string" && Number.isFinite(Date.parse(value)); }
function role(value: unknown): boolean { return typeof value === "string" && [...INVITE_ROLES, "owner"].includes(value); }

export function validateTeam(value: unknown, siteId: string): TeamState {
  if (!record(value) || !fields(value, ["schema_version", "site_id", "invitations", "members", "truncated", "can_revoke"]) ||
    value.schema_version !== 1 || value.site_id !== siteId || !id(value.site_id) ||
    typeof value.truncated !== "boolean" || typeof value.can_revoke !== "boolean" ||
    !Array.isArray(value.invitations) || value.invitations.length > 100 ||
    !Array.isArray(value.members) || value.members.length > 100 ||
    !value.invitations.every(i => record(i) && fields(i, ["id", "email", "role_key", "state", "created_at", "expires_at", "revocation_durability"]) &&
      id(i.id) && typeof i.email === "string" && i.email.length <= 320 && role(i.role_key) && i.role_key !== "owner" &&
      ["pending", "accepted", "revoked", "expired"].includes(String(i.state)) && date(i.created_at) && date(i.expires_at) &&
      (i.state === "revoked" ? ["pending", "acknowledged"].includes(String(i.revocation_durability)) : i.revocation_durability === null)) ||
    !value.members.every(m => record(m) && fields(m, ["user_id", "display_name", "role_key"]) && id(m.user_id) &&
      typeof m.display_name === "string" && m.display_name.length >= 1 && m.display_name.length <= 200 && role(m.role_key))) return { state: "unavailable" };
  return { state: "available", site_id: siteId, invitations: value.invitations, members: value.members,
    truncated: value.truncated, can_revoke: value.can_revoke } as TeamState;
}

export async function loadTeam({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<TeamState> {
  if (!TOKEN.test(tenantToken) || !id(siteId)) return { state: "unavailable" };
  try {
    const response = await relayJson(url(`/v1/sites/${siteId}/team`), request(`${TENANT_COOKIE_NAME}=${tenantToken}`), fetcher, 131072);
    if (response.status !== 200) throw new Error();
    return validateTeam(await json(response), siteId);
  } catch { return { state: "unavailable" }; }
}

export async function issueTeamInvitation({ tenantToken, siteId, email, roleKey, origin, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; email: string; roleKey: string; origin: string; fetcher?: typeof globalThis.fetch;
}): Promise<{ state: "created"; link: string; expiresAt: string } | { state: "denied" | "pending" | "unconfirmed" }> {
  if (!TOKEN.test(tenantToken) || !id(siteId) || !INVITE_ROLES.includes(roleKey as typeof INVITE_ROLES[number])) return { state: "denied" };
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(url("/v1/session/tenant-csrf"), request(cookie), fetcher, 131072);
    const csrf = await json(proof);
    if (proof.status !== 200 || !fields(csrf, ["schema_version", "csrf_token"]) || csrf.schema_version !== 1 ||
      typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) throw new Error();
    const response = await relayJson(url(`/v1/sites/${siteId}/invitations`), {
      ...request(cookie), method: "POST", headers: { Accept: "application/json", Cookie: cookie,
        "Content-Type": "application/json", Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token },
      body: JSON.stringify({ email, role_key: roleKey }),
    }, fetcher, 131072);
    if (response.status === 403) return { state: "denied" };
    if (response.status === 409) return { state: "pending" };
    if (response.status !== 201) throw new Error();
    const body = await json(response);
    if (!fields(body, ["schema_version", "invitation_id", "site_id", "token", "expires_at", "delivery"]) ||
      body.schema_version !== 1 || body.site_id !== siteId || !id(body.invitation_id) || typeof body.token !== "string" ||
      !TOKEN.test(body.token) || !date(body.expires_at) || body.delivery !== "not_emailed") throw new Error();
    return { state: "created", link: `${origin}/invitations/accept#${body.invitation_id}.${body.token}`, expiresAt: body.expires_at as string };
  } catch { return { state: "unconfirmed" }; }
}

export async function revokeTeamInvitation({ tenantToken, siteId, invitationId, confirmed, origin, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; invitationId: string; confirmed: boolean; origin: string; fetcher?: typeof globalThis.fetch;
}): Promise<{ state: "revoked" | "denied" | "unconfirmed" }> {
  if (!TOKEN.test(tenantToken) || !id(siteId) || !id(invitationId) || confirmed !== true) return { state: "denied" };
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const proof = await relayJson(url("/v1/session/tenant-csrf"), request(cookie), fetcher, 131072);
    const csrf = await json(proof);
    if (proof.status !== 200 || !fields(csrf, ["schema_version", "csrf_token"]) || csrf.schema_version !== 1 ||
      typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) throw new Error();
    const response = await relayJson(url(`/v1/sites/${siteId}/invitations/${invitationId}/revoke`), {
      ...request(cookie), method: "POST", headers: { Accept: "application/json", Cookie: cookie,
        "Content-Type": "application/json", Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token },
      body: "{}",
    }, fetcher, 131072);
    if (response.status === 403) return { state: "denied" };
    if (response.status !== 200) throw new Error();
    const body = await json(response);
    if (!fields(body, ["schema_version", "invitation_id", "site_id", "revoked_at", "durability"]) ||
      body.schema_version !== 1 || body.site_id !== siteId || body.invitation_id !== invitationId ||
      !date(body.revoked_at) || body.durability !== "AUTHORITY_DURABILITY_PENDING") throw new Error();
    return { state: "revoked" };
  } catch { return { state: "unconfirmed" }; }
}
