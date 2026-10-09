import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import type { DashboardSession } from "./session-api";
import { SESSION_COOKIE_NAME } from "./session-api";

const MAX_SITE_DIRECTORY_BYTES = 64 * 1024;
const MAX_SITES = 100;
const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CURRENCY = /^[A-Z]{3}$/;
const OWNERSHIP_STATES = new Set([
  "unverified",
  "verified",
  "reverification_required",
]);
const DIRECTORY_FIELDS = ["schema_version", "sites", "tenant_id", "tenant_name"];
const SITE_FIELDS = [
  "id",
  "name",
  "ownership_status",
  "primary_origin",
  "reporting_currency",
  "state",
  "timezone",
];

export interface DashboardSite {
  id: string;
  name: string;
  primaryOrigin: string;
  timezone: string;
  reportingCurrency: string;
  state: "onboarding" | "active";
  ownershipStatus: "unverified" | "verified" | "reverification_required";
}

export type DashboardSiteDirectory =
  | { state: "not_authenticated" }
  | { state: "unavailable" }
  | { state: "invalid" }
  | {
      state: "available";
      tenantId: string;
      tenantName: string;
      sites: DashboardSite[];
    };

interface LoadSiteDirectoryOptions {
  sessionTokens: readonly string[];
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

export async function loadDashboardSiteDirectory({
  sessionTokens,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: LoadSiteDirectoryOptions): Promise<DashboardSiteDirectory> {
  if (sessionTokens.length === 0) return { state: "not_authenticated" };
  if (sessionTokens.length !== 1 || !SESSION_TOKEN.test(sessionTokens[0] ?? "")) {
    return { state: "invalid" };
  }

  let baseUrl: URL;
  try {
    baseUrl = validatedSignalApiBaseUrl(
      configuredBaseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000",
    );
  } catch {
    return { state: "unavailable" };
  }

  let response: Response;
  try {
    response = await relayJson(new URL("/v1/sites", baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        Cookie: `${SESSION_COOKIE_NAME}=${sessionTokens[0]}`,
      },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 65536);
  } catch {
    return { state: "unavailable" };
  }

  if (response.status === 401) return { state: "not_authenticated" };
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };

  try {
    return await readSiteDirectory(response);
  } catch {
    return { state: "invalid" };
  }
}

export function reconcileDashboardSiteDirectory(
  session: DashboardSession,
  directory: DashboardSiteDirectory,
): DashboardSiteDirectory {
  if (session.state !== "authenticated") return { state: "not_authenticated" };
  if (directory.state === "not_authenticated") return { state: "invalid" };
  if (directory.state !== "available") return directory;
  if (directory.tenantId !== session.tenantId) return { state: "invalid" };
  if (
    session.activeSiteId !== null &&
    !directory.sites.some((site) => site.id === session.activeSiteId)
  ) {
    return { state: "invalid" };
  }
  return directory;
}

async function readSiteDirectory(response: Response): Promise<DashboardSiteDirectory> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim();
  if (contentType !== "application/json") throw new Error("Site directory content type invalid");
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_SITE_DIRECTORY_BYTES)
  ) {
    throw new Error("Site directory length invalid");
  }
  const body = await boundedRelayText(response, MAX_SITE_DIRECTORY_BYTES);
  if (new TextEncoder().encode(body).byteLength > MAX_SITE_DIRECTORY_BYTES) {
    throw new Error("Site directory response too large");
  }
  return validateSiteDirectory(JSON.parse(body));
}

function validateSiteDirectory(value: unknown): DashboardSiteDirectory {
  if (!hasExactFields(value, DIRECTORY_FIELDS)) throw new Error("Site directory invalid");
  if (
    value.schema_version !== 1 ||
    typeof value.tenant_id !== "string" ||
    !UUID.test(value.tenant_id) ||
    !validDisplayText(value.tenant_name, 200) ||
    !Array.isArray(value.sites) ||
    value.sites.length > MAX_SITES
  ) {
    throw new Error("Site directory invalid");
  }

  const identifiers = new Set<string>();
  const sites = value.sites.map((site): DashboardSite => {
    if (!hasExactFields(site, SITE_FIELDS)) throw new Error("Site entry invalid");
    if (
      typeof site.id !== "string" ||
      !UUID.test(site.id) ||
      identifiers.has(site.id) ||
      !validDisplayText(site.name, 200) ||
      !validOrigin(site.primary_origin) ||
      !validTimezone(site.timezone) ||
      typeof site.reporting_currency !== "string" ||
      !CURRENCY.test(site.reporting_currency) ||
      (site.state !== "onboarding" && site.state !== "active") ||
      typeof site.ownership_status !== "string" ||
      !OWNERSHIP_STATES.has(site.ownership_status)
    ) {
      throw new Error("Site entry invalid");
    }
    identifiers.add(site.id);
    return {
      id: site.id,
      name: site.name,
      primaryOrigin: site.primary_origin,
      timezone: site.timezone,
      reportingCurrency: site.reporting_currency,
      state: site.state,
      ownershipStatus: site.ownership_status as DashboardSite["ownershipStatus"],
    };
  });

  return {
    state: "available",
    tenantId: value.tenant_id,
    tenantName: value.tenant_name,
    sites,
  };
}

function validDisplayText(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= maximum &&
    value === value.trim() &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function validOrigin(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 2048 || /[\u0000-\u0020\u007f]/.test(value)) {
    return false;
  }
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.username === "" &&
      url.password === "" &&
      url.pathname === "/" &&
      url.search === "" &&
      url.hash === "" &&
      url.origin === value
    );
  } catch {
    return false;
  }
}

function validTimezone(value: unknown): value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_+\-/]{1,128}$/.test(value)) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format();
    return true;
  } catch {
    return false;
  }
}

function hasExactFields(value: unknown, fields: readonly string[]): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === fields.join(",")
  );
}
