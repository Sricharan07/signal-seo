import assert from "node:assert/strict";
import test from "node:test";

import { loadDashboardSnapshot } from "../lib/signal-api";

const fixedNow = () => new Date("2026-09-09T20:00:00.000Z");

test("loads readiness and the exact capability inventory", async () => {
  const fetcher: typeof fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/health/ready")) {
      return Response.json({ status: "ready", service: "signal-api", version: "0.0.0" });
    }
    return Response.json({
      schema_version: 1,
      release_status: "development",
      production_writes_enabled: false,
      capabilities: [
        { key: "identity.session_issuance", availability: "internal_only" },
        { key: "provider.production_writes", availability: "disabled" },
      ],
    });
  };

  const snapshot = await loadDashboardSnapshot({
    baseUrl: "http://signal-api.test",
    fetcher,
    now: fixedNow,
  });

  assert.deepEqual(snapshot, {
    fetchedAt: "2026-09-09T20:00:00.000Z",
    connection: "connected",
    dependencies: "ready",
    inventory: "available",
    releaseStatus: "development",
    productionWritesEnabled: false,
    capabilities: [
      { key: "identity.session_issuance", availability: "internal_only" },
      { key: "provider.production_writes", availability: "disabled" },
    ],
  });
});

test("shows a reachable but unready control plane without inventing readiness", async () => {
  const fetcher: typeof fetch = async (input) => {
    if (String(input).endsWith("/health/ready")) {
      return Response.json({ error: { code: "DEPENDENCIES_NOT_READY" } }, { status: 503 });
    }
    return Response.json({
      schema_version: 1,
      release_status: "development",
      production_writes_enabled: false,
      capabilities: [],
    });
  };

  const snapshot = await loadDashboardSnapshot({
    baseUrl: "https://signal-api.test/",
    fetcher,
    now: fixedNow,
  });

  assert.equal(snapshot.connection, "connected");
  assert.equal(snapshot.dependencies, "not_ready");
  assert.equal(snapshot.inventory, "available");
});

test("rejects malformed capability contracts and keeps writes disabled", async () => {
  const fetcher: typeof fetch = async (input) => {
    if (String(input).endsWith("/health/ready")) return new Response(null, { status: 200 });
    return Response.json({
      schema_version: 1,
      release_status: "development",
      production_writes_enabled: true,
      capabilities: [
        { key: "provider.production_writes", availability: "enabled" },
      ],
    });
  };

  const snapshot = await loadDashboardSnapshot({
    baseUrl: "http://signal-api.test",
    fetcher,
    now: fixedNow,
  });

  assert.equal(snapshot.connection, "connected");
  assert.equal(snapshot.inventory, "invalid");
  assert.equal(snapshot.productionWritesEnabled, false);
  assert.deepEqual(snapshot.capabilities, []);
});

test("fails closed for unreachable or credential-bearing API configuration", async () => {
  const unreachable = await loadDashboardSnapshot({
    baseUrl: "http://signal-api.test",
    fetcher: async () => {
      throw new Error("synthetic network failure containing internal detail");
    },
    now: fixedNow,
  });
  assert.equal(unreachable.connection, "unreachable");
  assert.equal(unreachable.inventory, "unavailable");
  assert.equal(unreachable.productionWritesEnabled, false);

  const misconfigured = await loadDashboardSnapshot({
    baseUrl: "https://token:secret@signal-api.test",
    fetcher: async () => assert.fail("Rejected configuration must not dispatch"),
    now: fixedNow,
  });
  assert.equal(misconfigured.connection, "misconfigured");
  assert.equal(misconfigured.productionWritesEnabled, false);
});

test("rejects oversized and duplicate capability inventories", async () => {
  const oversizedFetcher: typeof fetch = async (input) => {
    if (String(input).endsWith("/health/ready")) return new Response(null, { status: 200 });
    return new Response("{}", { headers: { "content-length": String(64 * 1024 + 1) } });
  };
  const oversized = await loadDashboardSnapshot({
    baseUrl: "http://signal-api.test",
    fetcher: oversizedFetcher,
    now: fixedNow,
  });
  assert.equal(oversized.inventory, "invalid");

  const duplicateFetcher: typeof fetch = async (input) => {
    if (String(input).endsWith("/health/ready")) return new Response(null, { status: 200 });
    return Response.json({
      schema_version: 1,
      release_status: "development",
      production_writes_enabled: false,
      capabilities: [
        { key: "identity.session_issuance", availability: "internal_only" },
        { key: "identity.session_issuance", availability: "internal_only" },
      ],
    });
  };
  const duplicate = await loadDashboardSnapshot({
    baseUrl: "http://signal-api.test",
    fetcher: duplicateFetcher,
    now: fixedNow,
  });
  assert.equal(duplicate.inventory, "invalid");
});

test("rejects a malformed declared capability length", async () => {
  const fetcher: typeof fetch = async (input) => {
    if (String(input).endsWith("/health/ready")) return new Response(null, { status: 200 });
    return new Response("{}", { headers: { "content-length": "not-a-length" } });
  };

  const snapshot = await loadDashboardSnapshot({
    baseUrl: "http://signal-api.test",
    fetcher,
    now: fixedNow,
  });

  assert.equal(snapshot.inventory, "invalid");
  assert.equal(snapshot.productionWritesEnabled, false);
});
