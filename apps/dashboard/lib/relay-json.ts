import { validatedSignalApiBaseUrl } from "./api-origin";

export class RelayResponseRejected extends Error {}

export function signalApiEndpoint(path: string, baseUrl?: string): URL {
  const base = validatedSignalApiBaseUrl(baseUrl ?? process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
  const url = new URL(path, base);
  if (!path.startsWith("/v1/") || url.origin !== base.origin || url.username || url.password || url.hash) throw new Error("Invalid API endpoint");
  return url;
}

export async function boundedRelayText(response: { body: ReadableStream<Uint8Array> | null; headers?: Headers }, maximum: number): Promise<string> {
  if (!Number.isSafeInteger(maximum) || maximum < 1 || response.headers?.has("set-cookie")) throw new Error("Invalid relay response");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Missing relay body");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > maximum) throw new Error("Oversized relay response");
      chunks.push(next.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally { reader.releaseLock(); }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder("utf-8", { fatal: true }).decode(body);
}

// Transport guarantees are shared; domain-specific response validators stay with their callers.
export async function relayJson(url: string | URL | Request, init: RequestInit, fetcher: typeof fetch = globalThis.fetch, maximum = 128 * 1024): Promise<Response> {
  if (!Number.isSafeInteger(maximum) || maximum < 1) throw new Error("Invalid relay bound");
  const endpoint = new URL(url instanceof Request ? url.url : url.toString());
  validatedSignalApiBaseUrl(endpoint.origin);
  if (!(endpoint.pathname.startsWith("/v1/") || ["/health/live", "/health/ready"].includes(endpoint.pathname)) || endpoint.username || endpoint.password || endpoint.hash) throw new Error("Invalid API endpoint");
  const response = await fetcher(endpoint, { ...init, cache: "no-store", redirect: init.redirect === "manual" ? "manual" : "error" });
  if (response.headers.has("set-cookie")) {
    await response.body?.cancel();
    throw new RelayResponseRejected("Unexpected relay cookie");
  }
  if (response.body === null) return response;
  const reader = response.body.getReader();
  let size = 0;
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const chunk = await reader.read();
        if (chunk.done) { reader.releaseLock(); controller.close(); return; }
        size += chunk.value.byteLength;
        if (size > maximum) throw new RelayResponseRejected("Oversized relay response");
        controller.enqueue(chunk.value);
      } catch (error) {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
        controller.error(error);
      }
    },
    async cancel(reason) { await reader.cancel(reason); reader.releaseLock(); },
  });
  return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
}
