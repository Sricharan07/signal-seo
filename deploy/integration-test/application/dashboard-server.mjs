import { readFileSync } from "node:fs";
import { createServer } from "node:https";
import next from "next";
import { dashboardServerOptions, readDashboardOrigin } from "./dashboard-server-options.mjs";

const configuredOrigin = readDashboardOrigin("/run/signal-config/dashboard-origin.json");
const app = next(dashboardServerOptions(process.env.SIGNAL_DASHBOARD_ORIGIN, configuredOrigin));
await app.prepare();
const server = createServer({
  key: readFileSync("/run/signal-tls/dashboard-key.pem"),
  cert: readFileSync("/run/signal-tls/dashboard.pem"),
  minVersion: "TLSv1.2",
  maxHeaderSize: 16384,
}, app.getRequestHandler());
server.headersTimeout = 10000;
server.requestTimeout = 15000;
server.keepAliveTimeout = 5000;
server.listen(8443, "0.0.0.0");
