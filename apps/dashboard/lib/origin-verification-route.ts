import type {
  OriginChallengeResult,
  OriginVerificationResult,
} from "./origin-verification-api";

const MAX_REQUEST_BYTES = 3072;

export async function readOriginMutation(
  request: Request,
  fields: readonly string[],
): Promise<Record<string, string | number> | null> {
  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    ?.trim();
  const declaredLength = request.headers.get("content-length");
  if (
    contentType !== "application/json" ||
    (declaredLength !== null &&
      (!/^\d+$/.test(declaredLength) ||
        Number(declaredLength) > MAX_REQUEST_BYTES))
  ) {
    return null;
  }
  try {
    const body = await request.text();
    if (new TextEncoder().encode(body).byteLength > MAX_REQUEST_BYTES) {
      return null;
    }
    const value: unknown = JSON.parse(body);
    if (
      typeof value !== "object" ||
      value === null ||
      Array.isArray(value) ||
      Object.keys(value).sort().join(",") !== fields.join(",")
    ) {
      return null;
    }
    const record = value as Record<string, unknown>;
    if (record.schema_version !== 1) return null;
    for (const field of fields) {
      if (field !== "schema_version" && typeof record[field] !== "string") {
        return null;
      }
    }
    return record as Record<string, string | number>;
  } catch {
    return null;
  }
}

export function originMutationResponse(
  result: OriginChallengeResult | OriginVerificationResult,
): Response {
  const status =
    result.state === "issued"
      ? 201
      : result.state === "verified"
        ? 200
        : result.state === "rejected"
          ? 403
          : result.state === "conflict"
            ? 409
            : result.state === "not_ready"
              ? 503
              : 500;
  return Response.json(result, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
