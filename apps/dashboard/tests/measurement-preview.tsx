import { DashboardView } from "../components/dashboard-view";
import { parseWeeklyReport } from "../lib/weekly-report-api";
import { measurementId, measurementReportFixture } from "./change-measurement-fixture";

export function measurementPreview() {
  const report = parseWeeklyReport(measurementReportFixture, measurementId, "2026-09-28");
  if (!report) throw new Error("Invalid synthetic measurement fixture.");
  return <DashboardView activeSection="changes" authNotice={null} localPilot={false}
    snapshot={{ fetchedAt: "2026-09-29T12:00:00Z", connection: "connected", dependencies: "ready", inventory: "available", releaseStatus: "development", productionWritesEnabled: false, capabilities: [] }}
    session={{ state: "authenticated", tenantId: measurementId, role: "owner", authenticationLevel: "mfa", expiresAt: "2026-09-30T10:00:00Z", sessionVersion: 1, activeSiteId: measurementId }}
    sites={{ state: "available", tenantId: measurementId, tenantName: "Synthetic measurement fixture", sites: [{ id: measurementId, name: "Synthetic test site", primaryOrigin: "https://example.invalid", timezone: "UTC", reportingCurrency: "USD", state: "active", ownershipStatus: "verified" }] }}
    organizations={{ state: "absent" }} weeklyReport={{ state: "available", report }} />;
}
