import assert from "node:assert/strict";
import test from "node:test";
import { boundedRelayText, relayJson, signalApiEndpoint } from "../lib/relay-json";

test("shared relay validates origins, refuses cookies, and cancels overflowing streams", async () => {
  assert.throws(() => signalApiEndpoint("//other.example.invalid/v1/sites", "http://127.0.0.1:8000"));
  assert.throws(() => signalApiEndpoint("/v1/sites", "https://synthetic-user@example.invalid"));
  let cancelled = false;
  let pulls = 0;
  const response = await relayJson(signalApiEndpoint("/v1/capabilities"), {}, async (_url, init) => {
    assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "error");
    return new Response(new ReadableStream<Uint8Array>({
      pull(controller) { pulls += 1; controller.enqueue(new Uint8Array(4)); },
      cancel() { cancelled = true; },
    }));
  }, 5);
  await assert.rejects(() => response.text(), /Oversized/);
  assert.equal(cancelled, true); assert.ok(pulls < 5);
  await assert.rejects(() => relayJson(signalApiEndpoint("/v1/capabilities"), {}, async () => Response.json({}, { headers: { "set-cookie": "synthetic-session=x" } })), /cookie/);
  const good = await relayJson(signalApiEndpoint("/v1/capabilities"), {}, async () => Response.json({ schema_version: 1 }), 100);
  assert.deepEqual(JSON.parse(await boundedRelayText(good, 100)), { schema_version: 1 });
  await assert.rejects(() => boundedRelayText(new Response(new Uint8Array([255])), 10));
  await assert.rejects(() => relayJson(signalApiEndpoint("/v1/capabilities"), {}, async () => Response.json({}), 0));
});
