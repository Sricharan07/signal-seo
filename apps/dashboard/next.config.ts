import type { NextConfig } from "next";

export function dashboardSecurityHeaders(
  environment = process.env.NODE_ENV,
  configuredProviderOrigin = process.env.SIGNAL_IDENTITY_PROVIDER_ORIGIN,
) {
  const scriptPolicy =
    environment === "development"
      ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
      : "script-src 'self' 'unsafe-inline'";
  const identityFormAction = validatedIdentityFormAction(
    configuredProviderOrigin,
    environment,
  );

  return [
    {
      key: "Content-Security-Policy",
      value: [
        "default-src 'self'",
        "base-uri 'self'",
        "connect-src 'self'",
        "font-src 'self'",
        `form-action 'self'${identityFormAction === null ? "" : ` ${identityFormAction}`}`,
        "frame-ancestors 'none'",
        "img-src 'self' data:",
        "object-src 'none'",
        scriptPolicy,
        "style-src 'self' 'unsafe-inline'",
      ].join("; "),
    },
    { key: "Referrer-Policy", value: "no-referrer" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
  ];
}

function validatedIdentityFormAction(value: string | undefined, environment: string | undefined) {
  if (value === undefined || value === "") return null;
  try {
    const origin = new URL(value);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
    if (
      value !== origin.origin ||
      origin.username !== "" ||
      origin.password !== "" ||
      (origin.protocol !== "https:" &&
        !(environment !== "production" && loopback && origin.protocol === "http:"))
    ) {
      throw new Error();
    }
    return origin.origin;
  } catch {
    return null;
  }
}

const nextConfig: NextConfig = {
  agentRules: false,
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: dashboardSecurityHeaders() }];
  },
};

export default nextConfig;
