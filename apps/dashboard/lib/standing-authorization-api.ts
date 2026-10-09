import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME, validatedDashboardOrigin } from "./browser-auth";

const TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_BYTES = 8192;

export interface StandingAuthorizationView {
  state: "no_grant" | "active" | "not_started" | "revoked" | "expired" | "recovery_stale";
  grantId: string | null;
  recipeReleaseIds: string[];
  workTypes: string[];
  thresholds: Record<string, number>;
  weeklyVolumeCaps: Record<string, number>;
  weeklyTotalCap: number | null;
  weeklySpendCents: number | null;
  excludedPaths: string[];
  startsAt: string | null;
  endsAt: string | null;
  recoveryWindowHours: number | null;
  restrictionEventId: string | null;
  durability: "ACKNOWLEDGED" | "AUTHORITY_DURABILITY_PENDING" | null;
}

export type StandingAuthorizationState =
  | { state: "available"; grant: StandingAuthorizationView }
  | { state: "unavailable" | "rejected" };

export type StandingMutationResult =
  | { state: "recorded"; grantId: string; recipeReleaseIds: string[] }
  | { state: "revoked"; restrictionEventId: string; durability: "ACKNOWLEDGED" | "AUTHORITY_DURABILITY_PENDING" }
  | { state: "not_ready" | "rejected" | "conflict" | "failed" };

export async function loadStandingAuthorization({
  tenantToken,
  siteId,
  fetcher = globalThis.fetch,
}: {
  tenantToken: string;
  siteId: string;
  fetcher?: typeof globalThis.fetch;
}): Promise<StandingAuthorizationState> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return { state: "rejected" };
  try {
    const response = await relayJson(
      signalApiEndpoint(`/v1/sites/${siteId}/standing-authorization`),
      {
        cache: "no-store",
        headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` },
        signal: AbortSignal.timeout(5000),
      }, fetcher, 8192);
    if (response.status !== 200) return { state: "unavailable" };
    const body = await boundedJson(response);
    if (!exact(body, [
      "durability", "ends_at", "excluded_paths", "grant_id", "recipe_release_ids",
      "recovery_window_hours", "restriction_event_id", "schema_version", "site_id",
      "starts_at", "state", "thresholds", "weekly_spend_cents",
      "weekly_total_cap", "weekly_volume_caps", "work_types",
    ]) || body.schema_version !== 1 || body.site_id !== siteId ||
      !["no_grant", "active", "not_started", "revoked", "expired", "recovery_stale"].includes(String(body.state)) ||
      !(body.grant_id === null || typeof body.grant_id === "string" && UUID.test(body.grant_id)) ||
      !Array.isArray(body.recipe_release_ids) ||
      !body.recipe_release_ids.every((id: unknown) => typeof id === "string" && UUID.test(id)) ||
      !Array.isArray(body.work_types) || !body.work_types.every((value: unknown) => typeof value === "string") ||
      !Array.isArray(body.excluded_paths) || !body.excluded_paths.every((value: unknown) => typeof value === "string") ||
      !plain(body.thresholds) || !plain(body.weekly_volume_caps) ||
      !(body.durability === null || body.durability === "ACKNOWLEDGED" || body.durability === "AUTHORITY_DURABILITY_PENDING") ||
      !(body.restriction_event_id === null || typeof body.restriction_event_id === "string" && UUID.test(body.restriction_event_id))
    ) return { state: "unavailable" };
    return {
      state: "available",
      grant: {
        state: body.state as StandingAuthorizationView["state"],
        grantId: body.grant_id as string | null,
        recipeReleaseIds: body.recipe_release_ids as string[],
        workTypes: body.work_types as string[],
        thresholds: body.thresholds as Record<string, number>,
        weeklyVolumeCaps: body.weekly_volume_caps as Record<string, number>,
        weeklyTotalCap: body.weekly_total_cap as number | null,
        weeklySpendCents: body.weekly_spend_cents as number | null,
        excludedPaths: body.excluded_paths as string[],
        startsAt: body.starts_at as string | null,
        endsAt: body.ends_at as string | null,
        recoveryWindowHours: body.recovery_window_hours as number | null,
        restrictionEventId: body.restriction_event_id as string | null,
        durability: body.durability as StandingAuthorizationView["durability"],
      },
    };
  } catch {
    return { state: "unavailable" };
  }
}

export async function mutateStandingAuthorization({
  tenantToken, siteId, grantId, body, dashboardOrigin, fetcher = globalThis.fetch,
}: {
  tenantToken: string;
  siteId: string;
  grantId?: string;
  body: Record<string, unknown>;
  dashboardOrigin: string;
  fetcher?: typeof globalThis.fetch;
}): Promise<StandingMutationResult> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId) ||
      (grantId !== undefined && !UUID.test(grantId))) return { state: "rejected" };
  let origin: string;
  try {
    origin = validatedDashboardOrigin(
      dashboardOrigin,
      dashboardOrigin.startsWith("http://") ? "development" : "production",
    );
    if (origin !== dashboardOrigin) return { state: "rejected" };
  } catch {
    return { state: "not_ready" };
  }
  const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
  let csrf: string;
  try {
    const response = await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), {
      cache: "no-store", headers: { Accept: "application/json", Cookie: cookie },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 8192);
    const data = await boundedJson(response);
    if (response.status !== 200 || !exact(data, ["csrf_token", "schema_version"]) ||
        data.schema_version !== 1 || typeof data.csrf_token !== "string" ||
        !TOKEN.test(data.csrf_token)) return { state: "not_ready" };
    csrf = data.csrf_token;
  } catch {
    return { state: "not_ready" };
  }
  try {
    const path = grantId === undefined
      ? `/v1/sites/${siteId}/standing-authorization`
      : `/v1/sites/${siteId}/standing-authorization/${grantId}/revoke`;
    const response = await relayJson(signalApiEndpoint(path), {
      method: "POST", cache: "no-store", signal: AbortSignal.timeout(15000),
      headers: {
        Accept: "application/json", "Content-Type": "application/json", Cookie: cookie,
        Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf,
      },
      body: JSON.stringify(body),
    }, fetcher, 8192);
    if (response.status === 201 || response.status === 202) {
      const data = await boundedJson(response);
      if (grantId === undefined && exact(data, ["grant_id", "recipe_release_ids", "schema_version", "site_id", "state"]) &&
          data.schema_version === 1 && data.site_id === siteId && data.state === "recorded" &&
          typeof data.grant_id === "string" && UUID.test(data.grant_id) &&
          Array.isArray(data.recipe_release_ids) && data.recipe_release_ids.every((id: unknown) => typeof id === "string" && UUID.test(id))) {
        return { state: "recorded", grantId: data.grant_id, recipeReleaseIds: data.recipe_release_ids };
      }
      if (grantId !== undefined && exact(data, ["durability", "grant_id", "restriction_event_id", "schema_version", "site_id"]) &&
          data.schema_version === 1 && data.site_id === siteId && data.grant_id === grantId &&
          typeof data.restriction_event_id === "string" && UUID.test(data.restriction_event_id) &&
          ["ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"].includes(String(data.durability))) {
        return { state: "revoked", restrictionEventId: data.restriction_event_id, durability: data.durability as "ACKNOWLEDGED" | "AUTHORITY_DURABILITY_PENDING" };
      }
      return { state: "failed" };
    }
    if (response.status === 401 || response.status === 403) return { state: "rejected" };
    if (response.status === 409) return { state: "conflict" };
    return { state: "not_ready" };
  } catch {
    return { state: "not_ready" };
  }
}

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(value: unknown, fields: string[]): value is Record<string, unknown> {
  return plain(value) && Object.keys(value).sort().join(",") === fields.sort().join(",");
}

async function boundedJson(response: Response): Promise<unknown> {
  if (response.headers.has("set-cookie")) throw new Error("Unexpected cookie");
  const text = await boundedRelayText(response, MAX_BYTES);
  if (new TextEncoder().encode(text).byteLength > MAX_BYTES) throw new Error("Oversized response");
  return JSON.parse(text) as unknown;
}
