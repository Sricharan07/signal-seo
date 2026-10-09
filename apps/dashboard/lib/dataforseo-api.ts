import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { signalApiEndpoint } from "./relay-json";
import { TENANT_COOKIE_NAME } from "./browser-auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
export type DataForSeoState = {
  availability: "available" | "unconfigured" | "cap_exhausted" | "secret_unavailable" | "execution_unavailable";
  credential_configured: boolean; execution_configured: boolean; cap_micros: number; usage_micros: number; month: string;
  features: Record<"competitor_gap" | "search_volume" | "competitor_backlinks", "available" | "unavailable">;
  records: Record<string, unknown>[];
} | { availability: "unavailable" };

export function dataforseoCommand(value: unknown): { siteId: string; command: Record<string, unknown> } | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const { site_id, ...command } = value as Record<string, unknown>;
  if (typeof site_id !== "string" || !UUID.test(site_id)) return null;
  const fields = Object.keys(command).sort().join(",");
  if (command.operation === "credential") {
    if (fields !== "login,operation,password" || typeof command.login !== "string" || !/^[!-9;-~]{1,254}$/.test(command.login) ||
        typeof command.password !== "string" || !/^[!-~]{8,512}$/.test(command.password)) return null;
  } else if (command.operation === "remove") {
    if (fields !== "operation") return null;
  } else if (command.operation === "cap") {
    if (fields !== "cap_micros,operation" || typeof command.cap_micros !== "number" || !Number.isSafeInteger(command.cap_micros) || command.cap_micros < 0 || command.cap_micros > 1000000000) return null;
  } else return null;
  return { siteId: site_id, command };
}


export function parseDataForSeo(value: unknown): DataForSeoState {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return { availability: "unavailable" };
  const data = value as Record<string, unknown>;
  if (Object.keys(data).sort().join(",") !== "availability,cap_micros,credential_configured,execution_configured,features,month,records,usage_micros" ||
      !["available", "unconfigured", "cap_exhausted", "secret_unavailable", "execution_unavailable"].includes(String(data.availability)) ||
      typeof data.credential_configured !== "boolean" || typeof data.execution_configured !== "boolean" ||
      typeof data.cap_micros !== "number" || !Number.isSafeInteger(data.cap_micros) || data.cap_micros < 0 || data.cap_micros > 1000000000 ||
      typeof data.usage_micros !== "number" || !Number.isSafeInteger(data.usage_micros) || data.usage_micros < 0 ||
      typeof data.month !== "string" || !/^\d{4}-\d{2}-01$/.test(data.month) || !Array.isArray(data.records) || data.records.length > 100 ||
      typeof data.features !== "object" || data.features === null || Array.isArray(data.features)) return { availability: "unavailable" };
  const features = data.features as Record<string, unknown>;
  if (Object.keys(features).sort().join(",") !== "competitor_backlinks,competitor_gap,search_volume" ||
      Object.values(features).some(value => value !== (data.availability === "available" ? "available" : "unavailable"))) return { availability: "unavailable" };
  return data as DataForSeoState;
}

async function document(response: Response): Promise<unknown> {
  if (response.status !== 200 || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") throw new Error();
  return JSON.parse(await boundedRelayText(response, 512000));
}

export async function loadDataForSeo({ tenantToken, siteId, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; fetcher?: typeof globalThis.fetch;
}): Promise<DataForSeoState> {
  if (!TOKEN.test(tenantToken) || !UUID.test(siteId)) return { availability: "unavailable" };
  try {
    return parseDataForSeo(await document(await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/dataforseo`), {
      cache: "no-store", headers: { Accept: "application/json", Cookie: `${TENANT_COOKIE_NAME}=${tenantToken}` }, signal: AbortSignal.timeout(5000),
    }, fetcher, 512000)));
  } catch { return { availability: "unavailable" }; }
}

export async function mutateDataForSeo({ tenantToken, siteId, origin, command, fetcher = globalThis.fetch }: {
  tenantToken: string; siteId: string; origin: string; command: Record<string, unknown>; fetcher?: typeof globalThis.fetch;
}): Promise<DataForSeoState> {
  if (!TOKEN.test(tenantToken) || !dataforseoCommand({ site_id: siteId, ...command })) return { availability: "unavailable" };
  try {
    const cookie = `${TENANT_COOKIE_NAME}=${tenantToken}`;
    const csrf = await document(await relayJson(signalApiEndpoint("/v1/session/tenant-csrf"), { cache: "no-store",
      headers: { Accept: "application/json", Cookie: cookie }, signal: AbortSignal.timeout(2500) }, fetcher, 512000)) as Record<string, unknown>;
    if (Object.keys(csrf).sort().join(",") !== "csrf_token,schema_version" || csrf.schema_version !== 1 || typeof csrf.csrf_token !== "string" || !TOKEN.test(csrf.csrf_token)) throw new Error();
    return parseDataForSeo(await document(await relayJson(signalApiEndpoint(`/v1/sites/${siteId}/dataforseo`), {
      method: "POST", cache: "no-store", signal: AbortSignal.timeout(15000), headers: {
        Accept: "application/json", Cookie: cookie, "Content-Type": "application/json", Origin: origin,
        "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf.csrf_token }, body: JSON.stringify(command),
    }, fetcher, 512000)));
  } catch { return { availability: "unavailable" }; }
}
