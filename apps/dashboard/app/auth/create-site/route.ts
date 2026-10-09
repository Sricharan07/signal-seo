import {
  TENANT_COOKIE_NAME,
  dashboardMutationAccepted,
  exactCookie,
  onboardSignalSite,
  validatedDashboardOrigin,
} from "@/lib/browser-auth";
import {
  authRelayResponse,
  identityServiceUnavailableResponse,
  localNoticeResponse,
  rejectedMutationResponse,
} from "@/lib/auth-route";

const MAX_ONBOARDING_BYTES = 4096;
const ONBOARDING_FIELDS = [
  "expected_session_version",
  "idempotency_key",
  "name",
  "primary_origin",
  "reporting_currency",
  "timezone",
] as const;

export async function POST(request: Request): Promise<Response> {
  let dashboardOrigin: string;
  try {
    dashboardOrigin = validatedDashboardOrigin();
  } catch {
    return identityServiceUnavailableResponse();
  }
  if (!dashboardMutationAccepted(request, dashboardOrigin)) {
    return rejectedMutationResponse();
  }
  const proposed = await onboardingRequest(request);
  const tenantToken = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (proposed === null || tenantToken === null) {
    return localNoticeResponse(dashboardOrigin, "site-create-rejected");
  }
  const result = await onboardSignalSite({
    tenantToken,
    idempotencyKey: proposed.idempotencyKey,
    name: proposed.name,
    primaryOrigin: proposed.primaryOrigin,
    timezone: proposed.timezone,
    reportingCurrency: proposed.reportingCurrency,
    expectedSessionVersion: proposed.expectedSessionVersion,
    dashboardOrigin,
  });
  return authRelayResponse(dashboardOrigin, result, {
    not_ready: "site-create-failed",
    rejected: "site-create-rejected",
    conflict: "site-create-conflict",
    failed: "site-create-failed",
  });
}

async function onboardingRequest(request: Request): Promise<{
  idempotencyKey: string;
  name: string;
  primaryOrigin: string;
  timezone: string;
  reportingCurrency: string;
  expectedSessionVersion: number;
} | null> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0]?.trim();
  const declaredLength = request.headers.get("content-length");
  if (
    contentType !== "application/x-www-form-urlencoded" ||
    (declaredLength !== null &&
      (!/^\d+$/.test(declaredLength) || Number(declaredLength) > MAX_ONBOARDING_BYTES))
  ) {
    return null;
  }
  try {
    const body = await request.text();
    if (new TextEncoder().encode(body).byteLength > MAX_ONBOARDING_BYTES) return null;
    const form = new URLSearchParams(body);
    if (
      [...form.keys()].some(
        (key) => !ONBOARDING_FIELDS.includes(key as (typeof ONBOARDING_FIELDS)[number]),
      ) ||
      ONBOARDING_FIELDS.some((field) => form.getAll(field).length !== 1)
    ) {
      return null;
    }
    const idempotencyKey = form.get("idempotency_key");
    const name = form.get("name");
    const primaryOrigin = form.get("primary_origin");
    const timezone = form.get("timezone");
    const reportingCurrency = form.get("reporting_currency");
    const rawVersion = form.get("expected_session_version");
    if (
      idempotencyKey === null ||
      name === null ||
      primaryOrigin === null ||
      timezone === null ||
      reportingCurrency === null ||
      rawVersion === null ||
      !/^[1-9]\d{0,15}$/.test(rawVersion)
    ) {
      return null;
    }
    const expectedSessionVersion = Number(rawVersion);
    return Number.isSafeInteger(expectedSessionVersion)
      ? {
          idempotencyKey,
          name,
          primaryOrigin,
          timezone,
          reportingCurrency,
          expectedSessionVersion,
        }
      : null;
  } catch {
    return null;
  }
}
