import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { IDENTITY_COOKIE_NAME } from "./browser-auth";

const MAX_ORGANIZATION_BYTES = 64 * 1024;
const MAX_ORGANIZATIONS = 100;
const SESSION_TOKEN = /^[A-Za-z0-9_-]{43}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ROLES = new Set(["viewer", "analyst", "editor", "approver", "admin", "owner"]);

export interface DashboardOrganization {
  tenantId: string;
  name: string;
  role: "viewer" | "analyst" | "editor" | "approver" | "admin" | "owner";
}

export type DashboardOrganizationDirectory =
  | { state: "absent" }
  | { state: "expired" }
  | { state: "unavailable" }
  | { state: "invalid" }
  | { state: "available"; organizations: DashboardOrganization[] };

interface LoadOrganizationOptions {
  identityTokens: readonly string[];
  baseUrl?: string;
  fetcher?: typeof globalThis.fetch;
}

export async function loadDashboardOrganizations({
  identityTokens,
  baseUrl: configuredBaseUrl,
  fetcher = globalThis.fetch,
}: LoadOrganizationOptions): Promise<DashboardOrganizationDirectory> {
  if (identityTokens.length === 0) return { state: "absent" };
  if (identityTokens.length !== 1 || !SESSION_TOKEN.test(identityTokens[0] ?? "")) {
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
    response = await relayJson(new URL("/v1/organizations", baseUrl), {
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        Cookie: `${IDENTITY_COOKIE_NAME}=${identityTokens[0]}`,
      },
      signal: AbortSignal.timeout(2500),
    }, fetcher, 65536);
  } catch {
    return { state: "unavailable" };
  }
  if (response.status === 401) return { state: "expired" };
  if (response.status === 503) return { state: "unavailable" };
  if (response.status !== 200) return { state: "invalid" };

  try {
    return await readOrganizationDirectory(response);
  } catch {
    return { state: "invalid" };
  }
}

async function readOrganizationDirectory(
  response: Response,
): Promise<DashboardOrganizationDirectory> {
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim();
  if (contentType !== "application/json") throw new Error("Invalid content type");
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_ORGANIZATION_BYTES)
  ) {
    throw new Error("Invalid organization response length");
  }
  const body = await boundedRelayText(response, MAX_ORGANIZATION_BYTES);
  if (new TextEncoder().encode(body).byteLength > MAX_ORGANIZATION_BYTES) {
    throw new Error("Organization response too large");
  }
  return validateOrganizationDirectory(JSON.parse(body));
}

function validateOrganizationDirectory(value: unknown): DashboardOrganizationDirectory {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Invalid organization directory");
  }
  const document = value as Record<string, unknown>;
  if (
    Object.keys(document).sort().join(",") !== "organizations,schema_version" ||
    document.schema_version !== 1 ||
    !Array.isArray(document.organizations) ||
    document.organizations.length > MAX_ORGANIZATIONS
  ) {
    throw new Error("Invalid organization directory");
  }
  const identifiers = new Set<string>();
  const organizations = document.organizations.map((entry: unknown): DashboardOrganization => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("Invalid organization entry");
    }
    const organization = entry as Record<string, unknown>;
    if (
      Object.keys(organization).sort().join(",") !== "name,role_key,tenant_id" ||
      typeof organization.tenant_id !== "string" ||
      !UUID.test(organization.tenant_id) ||
      identifiers.has(organization.tenant_id) ||
      !validDisplayText(organization.name) ||
      typeof organization.role_key !== "string" ||
      !ROLES.has(organization.role_key)
    ) {
      throw new Error("Invalid organization entry");
    }
    identifiers.add(organization.tenant_id as string);
    return {
      tenantId: organization.tenant_id as string,
      name: organization.name as string,
      role: organization.role_key as DashboardOrganization["role"],
    };
  });
  return { state: "available", organizations };
}

function validDisplayText(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= 200 &&
    value === value.trim() &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}
