import type { ChangeMeasurement } from "./change-measurement-api";
import { loadFactsInsight, loadSeoInsight, loadVisibilityInsight, loadWriterInsight, type OwnerInsights } from "./owner-insights";
import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";

import type { AuthNoticeKey } from "./auth-route";
import { IDENTITY_COOKIE_NAME } from "./browser-auth";
import { loadDashboardFindings, loadLatestPageObservation } from "./finding-api";
import { loadDashboardOrganizations } from "./organization-api";
import {
  type DashboardProposalNotice,
  loadDashboardProposals,
} from "./proposal-api";
import { loadDashboardCandidateInbox } from "./candidate-inbox-api";
import { loadDashboardGithubPrOperations } from "./github-pr-operations-api";
import { loadDashboardDeliveryObservations } from "./github-delivery-api";
import { loadDashboardSession, SESSION_COOKIE_NAME } from "./session-api";
import { loadDashboardSnapshot } from "./signal-api";
import { loadStandingAuthorization } from "./standing-authorization-api";
import { loadSlack } from "./slack-api";
import { loadOwnerConnector, type BingState, type GscState, type GithubState, type GithubPrState } from "./owner-connectors-api";
import { loadWeeklyPause } from "./weekly-loop-api";
import { loadDataForSeo } from "./dataforseo-api";
import { loadIndexNow } from "./indexnow-api";
import { loadEmail } from "./email-api";
import { loadTeam } from "./team-api";
import { loadChatReports } from "./chat-reports-api";
import { loadAssistantPage, NO_ASSISTANT } from "./assistant-api";

import { loadHealth } from "./health-api";
import { loadTelegram } from "./telegram-api";
import { loadDocs } from "./docs-api";
import {
  loadDashboardSiteDirectory,
  reconcileDashboardSiteDirectory,
} from "./site-api";
import { loadDashboardWork } from "./work-api";
import { loadWeeklyReport } from "./weekly-report-api";

const AUTH_NOTICES = new Set<AuthNoticeKey>([
  "browser-cleared",
  "callback-failed",
  "callback-rejected",
  "identity-not-ready",
  "identity-ready",
  "logged-out",
  "logout-failed",
  "logout-rejected",
  "organization-failed",
  "organization-rejected",
  "request-rejected",
  "site-conflict",
  "site-create-conflict",
  "site-create-failed",
  "site-create-rejected",
  "site-created",
  "site-failed",
  "site-rejected",
  "site-selected",
  "signed-in",
]);
const PROPOSAL_NOTICES = new Set<DashboardProposalNotice>([
  "prepared",
  "approved",
  "rejected",
  "changes_requested",
  "request_rejected",
  "not_authenticated",
  "unavailable",
  "invalid",
  "conflict",
]);

export async function loadDashboardPage(
  searchParams?: Promise<Record<string, string | string[] | undefined>>,
  section = "overview",
) {
  const emptyParams: Record<string, string | string[] | undefined> = {};
  const cookieStore = await cookies();
  const sessionTokens = cookieStore
    .getAll(SESSION_COOKIE_NAME)
    .map(({ value }) => value);
  const identityTokens = cookieStore
    .getAll(IDENTITY_COOKIE_NAME)
    .map(({ value }) => value);
  const [snapshot, session, unverifiedDirectory, organizations, query] =
    await Promise.all([
      loadDashboardSnapshot(),
      loadDashboardSession({ sessionTokens }),
      loadDashboardSiteDirectory({ sessionTokens }),
      loadDashboardOrganizations({ identityTokens }),
      searchParams ?? Promise.resolve(emptyParams),
    ]);
  const sites = reconcileDashboardSiteDirectory(session, unverifiedDirectory);
  const localPilot = process.env.SIGNAL_LOCAL_PILOT === "1";
  const localVerifiedCrawl =
    localPilot && process.env.SIGNAL_LOCAL_PILOT_VERIFIED_CRAWL === "1";
  const [work, findings, pageObservation, proposals, candidateInbox, githubPrOperations, deliveryObservations] =
    session.state === "authenticated" && session.activeSiteId !== null
      ? await Promise.all([
          loadDashboardWork({
            tenantToken: sessionTokens[0] ?? "",
            siteId: session.activeSiteId,
          }),
          localPilot
            ? loadDashboardFindings({
                tenantToken: sessionTokens[0] ?? "",
                siteId: session.activeSiteId,
              })
            : Promise.resolve({ state: "available" as const, findings: [] }),
          localPilot
            ? loadLatestPageObservation({
                tenantToken: sessionTokens[0] ?? "",
                siteId: session.activeSiteId,
              })
            : Promise.resolve({ state: "available" as const, observation: null }),
          localPilot
            ? loadDashboardProposals({
                tenantToken: sessionTokens[0] ?? "",
                siteId: session.activeSiteId,
              })
            : Promise.resolve({ state: "available" as const, proposals: [] }),
          loadDashboardCandidateInbox({
                tenantToken: sessionTokens[0] ?? "",
                siteId: session.activeSiteId,
              }),
          loadDashboardGithubPrOperations({
                tenantToken: sessionTokens[0] ?? "",
                siteId: session.activeSiteId,
              }),
          loadDashboardDeliveryObservations({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId }),
        ])
      : [
          { state: "no_work" as const },
          { state: "available" as const, findings: [] },
          { state: "available" as const, observation: null },
          { state: "available" as const, proposals: [] },
          { state: "available" as const, revisions: [] },
          { state: "available" as const, operations: [] },
          { state: "available" as const, observations: [] },
        ];
  const standingAuthorization =
    session.state === "authenticated" && session.role === "owner" &&
    session.activeSiteId !== null
      ? await loadStandingAuthorization({
          tenantToken: sessionTokens[0] ?? "",
          siteId: session.activeSiteId,
        })
      : { state: "rejected" as const };
  const slack = session.state === "authenticated" && session.activeSiteId !== null
    ? await loadSlack({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { availability: "unavailable" as const };
  const connectorScope = session.state === "authenticated" && session.role === "owner" && session.activeSiteId !== null
    ? { tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId } : null;
  const health = connectorScope === null ? { state: "rejected" as const } : await loadHealth(connectorScope);
  const team = connectorScope === null ? { state: "unavailable" as const } : await loadTeam(connectorScope);
  const gsc: GscState = connectorScope === null ? { availability: "unavailable" }
    : await loadOwnerConnector({ ...connectorScope, connector: "gsc" }) as GscState;
  const github: GithubState = connectorScope === null ? { availability: "unavailable" }
    : await loadOwnerConnector({ ...connectorScope, connector: "github" }) as GithubState;
  const githubPr: GithubPrState = connectorScope === null ? { availability: "unavailable" }
    : await loadOwnerConnector({ ...connectorScope, connector: "github-pr" }) as GithubPrState;
  const weeklyPause = connectorScope === null ? { state: "unavailable" as const }
    : await loadWeeklyPause(connectorScope);
  const bing: BingState = connectorScope === null ? { availability: "unavailable" }
    : await loadOwnerConnector({ ...connectorScope, connector: "bing" }) as BingState;
  const email = session.state === "authenticated" && session.activeSiteId !== null
    ? await loadEmail({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { availability: "unavailable" as const };
  const chatReports = connectorScope === null ? { availability: "unavailable" as const }
    : await loadChatReports(connectorScope);
  const dataforseo = session.state === "authenticated" && session.role === "owner" && session.activeSiteId !== null
    ? await loadDataForSeo({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { availability: "unavailable" as const };
  const indexnow = session.state === "authenticated" && session.role === "owner" && session.activeSiteId !== null
    ? await loadIndexNow({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { state: "rejected" as const };
  const docs = session.state === "authenticated" && session.role === "owner" && session.activeSiteId !== null
    ? await loadDocs({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { availability: "unavailable" as const, sources: [] };
  const weeklyReport = session.state === "authenticated" && session.role === "owner" && session.activeSiteId !== null
    ? await loadWeeklyReport({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { state: "rejected" as const };
  const telegram = session.state === "authenticated" && session.activeSiteId !== null
    ? await loadTelegram({ tenantToken: sessionTokens[0] ?? "", siteId: session.activeSiteId })
    : { availability: "unavailable" as const };
  const insights = await loadOwnerInsights(connectorScope, section);
  const assistant = section === "chat" && session.state === "authenticated" && session.activeSiteId !== null
    ? await loadAssistantPage(sessionTokens[0] ?? "", session.activeSiteId, query.conversation) : NO_ASSISTANT;
  const measurementHistory = section === "analytics" && connectorScope !== null && weeklyReport.state === "available"
    ? await loadMeasurementHistory(connectorScope, weeklyReport.report.weekStart, weeklyReport.report.measurements)
    : weeklyReport.state === "available" ? weeklyReport.report.measurements : [];
  return {
    snapshot,
    session,
    sites,
    organizations,
    work,
    findings,
    pageObservation,
    proposals,
    standingAuthorization,
    slack,
    gsc,
    github,
    githubPr,
    weeklyPause,
    bing,
    dataforseo,
    indexnow,
    email,
    team,
    chatReports,

    health,
    telegram,
    docs,
    candidateInbox,
    githubPrOperations,
    deliveryObservations,
    weeklyReport,
    insights,
    assistant,
    measurementHistory,
    localPilot,
    localVerifiedCrawl,
    authNotice: authNotice(query.auth),
    proposalNotice: proposalNotice(query.approval ?? query.proposal),
    siteOnboardingRequestId: randomUUID(),
    proposalDecisionId: randomUUID(),
    candidateDecisionId: randomUUID(),
    selectedCandidateRevisionId: typeof query.revision === "string" ? query.revision : undefined,
    activityFilter: (["shipped", "drafted", "decided", "measured", "held"].includes(String(query.kind)) ? query.kind : "all") as "all" | "shipped" | "drafted" | "decided" | "measured" | "held",
    inboxSelection: {
      article: typeof query.article === "string" ? query.article : undefined,
      fact: typeof query.fact === "string" ? query.fact : undefined,
      kind: (query.kind === "fix" || query.kind === "article" || query.kind === "fact" ? query.kind : undefined) as "fix" | "article" | "fact" | undefined,
    },
  };
}

const INSIGHT_SECTIONS: Record<string, readonly (keyof OwnerInsights)[]> = {
  overview: ["seo", "visibility", "writer", "facts"],
  approvals: ["seo", "writer", "facts"],
  analytics: ["seo", "visibility"],
  "content-writer": ["seo", "writer"],
  changes: ["writer"],
};

/** The owner reads a page needs, in parallel; a page never waits for data it does not show. */
async function loadOwnerInsights(scope: { tenantToken: string; siteId: string } | null, section: string): Promise<OwnerInsights> {
  const wanted = scope === null ? [] : INSIGHT_SECTIONS[section] ?? [];
  const skip = Promise.resolve({ state: "unavailable" as const });
  const [seo, visibility, writer, facts] = await Promise.all([
    wanted.includes("seo") && scope ? loadSeoInsight(scope.tenantToken, scope.siteId) : skip,
    wanted.includes("visibility") && scope ? loadVisibilityInsight(scope.tenantToken, scope.siteId) : skip,
    wanted.includes("writer") && scope ? loadWriterInsight(scope.tenantToken, scope.siteId) : skip,
    wanted.includes("facts") && scope ? loadFactsInsight(scope.tenantToken, scope.siteId) : skip,
  ]);
  return { seo, visibility, writer, facts };
}

/** Measurements from the latest twelve earlier weekly reports, so Results can show every measured change
    (a change is measured 7, 28 and 90 days after it goes live, in whichever week that falls). */
async function loadMeasurementHistory(scope: { tenantToken: string; siteId: string }, latestWeek: string, current: ChangeMeasurement[]): Promise<ChangeMeasurement[]> {
  const start = Date.parse(`${latestWeek}T00:00:00Z`);
  if (!Number.isFinite(start)) return current;
  const weeks = Array.from({ length: 12 }, (_, index) => new Date(start - (index + 1) * 7 * 86_400_000).toISOString().slice(0, 10));
  const reports = await Promise.all(weeks.map((weekStart) => loadWeeklyReport({ ...scope, weekStart })));
  return [...current, ...reports.flatMap((report) => report.state === "available" ? report.report.measurements : [])];
}

function proposalNotice(
  value: string | string[] | undefined,
): DashboardProposalNotice | null {
  return typeof value === "string" && PROPOSAL_NOTICES.has(value as DashboardProposalNotice)
    ? (value as DashboardProposalNotice)
    : null;
}

function authNotice(
  value: string | string[] | undefined,
): AuthNoticeKey | null {
  return typeof value === "string" && AUTH_NOTICES.has(value as AuthNoticeKey)
    ? (value as AuthNoticeKey)
    : null;
}
