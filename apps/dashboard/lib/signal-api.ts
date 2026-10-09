import { boundedRelayText, relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";

const MAX_CAPABILITY_BYTES = 64 * 1024;
const MAX_CAPABILITIES = 128;
const CAPABILITY_KEY = /^[a-z][a-z0-9_.-]{0,95}$/;

export type CapabilityAvailability = "disabled" | "internal_only";

export interface SignalCapability {
  key: string;
  availability: CapabilityAvailability;
}

export interface DashboardSnapshot {
  fetchedAt: string;
  connection: "connected" | "unreachable" | "misconfigured";
  dependencies: "ready" | "not_ready" | "unknown";
  inventory: "available" | "invalid" | "unavailable";
  releaseStatus: string;
  productionWritesEnabled: boolean;
  capabilities: SignalCapability[];
}

type Fetcher = typeof globalThis.fetch;

interface LoadOptions {
  baseUrl?: string;
  fetcher?: Fetcher;
  now?: () => Date;
}

interface CapabilitiesDocument {
  schema_version: 1;
  release_status: string;
  production_writes_enabled: boolean;
  capabilities: SignalCapability[];
}

export async function loadDashboardSnapshot(options: LoadOptions = {}): Promise<DashboardSnapshot> {
  const now = options.now ?? (() => new Date());
  const fallback = baseSnapshot(now);
  let baseUrl: URL;
  try {
    baseUrl = validatedSignalApiBaseUrl(
      options.baseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000",
    );
  } catch {
    return { ...fallback, connection: "misconfigured" };
  }

  const fetcher = options.fetcher ?? globalThis.fetch;
  const request = { cache: "no-store" as const, redirect: "error" as const };
  const [healthResult, capabilitiesResult] = await Promise.allSettled([
    relayJson(new URL("/health/ready", baseUrl), {
      ...request,
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(2500),
    }, fetcher, MAX_CAPABILITY_BYTES),
    relayJson(new URL("/v1/capabilities", baseUrl), {
      ...request,
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(2500),
    }, fetcher, MAX_CAPABILITY_BYTES),
  ]);

  const connection =
    healthResult.status === "fulfilled" || capabilitiesResult.status === "fulfilled"
      ? "connected"
      : "unreachable";
  const dependencies =
    healthResult.status !== "fulfilled"
      ? "unknown"
      : healthResult.value.status === 200
        ? "ready"
        : healthResult.value.status === 503
          ? "not_ready"
          : "unknown";

  if (capabilitiesResult.status !== "fulfilled") {
    return { ...fallback, connection, dependencies };
  }

  try {
    const document = await readCapabilities(capabilitiesResult.value);
    return {
      fetchedAt: fallback.fetchedAt,
      connection,
      dependencies,
      inventory: "available",
      releaseStatus: document.release_status,
      productionWritesEnabled: document.production_writes_enabled,
      capabilities: document.capabilities,
    };
  } catch {
    return { ...fallback, connection, dependencies, inventory: "invalid" };
  }
}

function baseSnapshot(now: () => Date): DashboardSnapshot {
  return {
    fetchedAt: now().toISOString(),
    connection: "unreachable",
    dependencies: "unknown",
    inventory: "unavailable",
    releaseStatus: "unknown",
    productionWritesEnabled: false,
    capabilities: [],
  };
}

async function readCapabilities(response: Response): Promise<CapabilitiesDocument> {
  if (response.status !== 200) {
    throw new Error("Capability inventory unavailable");
  }
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_CAPABILITY_BYTES)
  ) {
    throw new Error("Capability inventory length invalid");
  }
  const body = await boundedRelayText(response, MAX_CAPABILITY_BYTES);
  if (new TextEncoder().encode(body).byteLength > MAX_CAPABILITY_BYTES) {
    throw new Error("Capability inventory too large");
  }
  return validateCapabilities(JSON.parse(body));
}

function validateCapabilities(value: unknown): CapabilitiesDocument {
  if (!isRecord(value)) {
    throw new Error("Invalid capability inventory");
  }
  const keys = Object.keys(value).sort();
  const expected = [
    "capabilities",
    "production_writes_enabled",
    "release_status",
    "schema_version",
  ];
  if (JSON.stringify(keys) !== JSON.stringify(expected)) {
    throw new Error("Invalid capability inventory");
  }
  if (
    value.schema_version !== 1 ||
    typeof value.release_status !== "string" ||
    !/^[a-z][a-z0-9_-]{0,31}$/.test(value.release_status) ||
    typeof value.production_writes_enabled !== "boolean" ||
    !Array.isArray(value.capabilities) ||
    value.capabilities.length > MAX_CAPABILITIES
  ) {
    throw new Error("Invalid capability inventory");
  }
  const seen = new Set<string>();
  const capabilities = value.capabilities.map((entry): SignalCapability => {
    if (
      !isRecord(entry) ||
      Object.keys(entry).sort().join(",") !== "availability,key" ||
      typeof entry.key !== "string" ||
      !CAPABILITY_KEY.test(entry.key) ||
      (entry.availability !== "disabled" && entry.availability !== "internal_only") ||
      seen.has(entry.key)
    ) {
      throw new Error("Invalid capability inventory");
    }
    seen.add(entry.key);
    return { key: entry.key, availability: entry.availability };
  });
  return {
    schema_version: 1,
    release_status: value.release_status,
    production_writes_enabled: value.production_writes_enabled,
    capabilities,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
