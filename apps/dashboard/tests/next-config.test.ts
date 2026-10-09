import assert from "node:assert/strict";
import test from "node:test";

import nextConfig, { dashboardSecurityHeaders } from "../next.config";

test("production headers deny framing and omit development eval authority", async () => {
  const headers = dashboardSecurityHeaders("production");
  const values = Object.fromEntries(headers.map(({ key, value }) => [key, value]));

  assert.match(values["Content-Security-Policy"], /frame-ancestors 'none'/);
  assert.match(values["Content-Security-Policy"], /object-src 'none'/);
  assert.doesNotMatch(values["Content-Security-Policy"], /unsafe-eval/);
  assert.equal(values["Referrer-Policy"], "no-referrer");
  assert.equal(values["X-Content-Type-Options"], "nosniff");
  assert.equal(values["X-Frame-Options"], "DENY");
  assert.equal(nextConfig.poweredByHeader, false);

  const headersFunction = nextConfig.headers;
  assert.equal(typeof headersFunction, "function");
  if (headersFunction === undefined) assert.fail("dashboard headers are required");
  const routes = await headersFunction();
  assert.equal(routes[0]?.source, "/:path*");
});

test("development permits only the extra eval capability required by React diagnostics", () => {
  const production = dashboardSecurityHeaders("production");
  const development = dashboardSecurityHeaders("development");
  const productionCsp = production.find(({ key }) => key === "Content-Security-Policy")?.value;
  const developmentCsp = development.find(({ key }) => key === "Content-Security-Policy")?.value;

  assert.match(developmentCsp ?? "", /script-src 'self' 'unsafe-inline' 'unsafe-eval'/);
  assert.equal(developmentCsp?.replace(" 'unsafe-eval'", ""), productionCsp);
});

test("form actions allow only the exact configured identity origin", () => {
  const production = dashboardSecurityHeaders("production", "https://identity.signal.test");
  const localPilot = dashboardSecurityHeaders("development", "http://127.0.0.1:49152");
  const rejected = dashboardSecurityHeaders("production", "http://identity.signal.test");
  const csp = (headers: ReturnType<typeof dashboardSecurityHeaders>) =>
    headers.find(({ key }) => key === "Content-Security-Policy")?.value ?? "";

  assert.match(csp(production), /form-action 'self' https:\/\/identity\.signal\.test/);
  assert.match(csp(localPilot), /form-action 'self' http:\/\/127\.0\.0\.1:49152/);
  assert.match(csp(rejected), /form-action 'self';/);
  assert.doesNotMatch(csp(rejected), /identity\.signal\.test/);
});
