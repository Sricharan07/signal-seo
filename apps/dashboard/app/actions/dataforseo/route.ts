import { TENANT_COOKIE_NAME, dashboardMutationAccepted, exactCookie, validatedDashboardOrigin } from "@/lib/browser-auth";
import { dataforseoCommand, mutateDataForSeo } from "@/lib/dataforseo-api";

export async function POST(request: Request): Promise<Response> {
  const fail = (status: number) => Response.json({ availability: "unavailable" }, { status, headers: { "Cache-Control": "no-store" } });
  let origin: string;
  try { origin = validatedDashboardOrigin(); } catch { return fail(503); }
  const token = exactCookie(request.headers.get("cookie"), TENANT_COOKIE_NAME);
  if (!token || !dashboardMutationAccepted(request, origin) || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")) return fail(403);
  try {
    const reader = request.body?.getReader();
    if (!reader) return fail(403);
    const chunks: Uint8Array[] = []; let size = 0;
    for (;;) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength;
      if (size > 2048) { await reader.cancel(); return fail(403); }
      chunks.push(next.value);
    }
    const buffer = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
    const command = dataforseoCommand(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer)));
    if (!command) return fail(403);
    const state = await mutateDataForSeo({ tenantToken: token, siteId: command.siteId, origin, command: command.command });
    return state.availability === "unavailable" ? fail(503) : Response.json(state, { headers: { "Cache-Control": "no-store" } });
  } catch { return fail(403); }
}
