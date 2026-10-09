import {
  BarChart3 as Analytics,
  Brain,
  FilePenLine,
  ArrowRight,
  BadgeCheck as Authority,
  BookOpenCheck as Recipes,
  Building2 as Organization,
  Cable as Connectors,
  CircleCheck as Verified,
  CircleHelp as Help,
  CircleSlash2 as Unavailable,
  ClipboardCheck as Approvals,
  Database as Usage,
  FileText as Pages,
  FileCheck2,
  Gauge as Meter,
  GitPullRequest as Changes,
  Globe2 as Origin,
  LayoutDashboard as Overview,
  ListChecks as Work,
  LogIn as SignIn,
  LogOut as SignOut,
  MessageSquareText as Chat,
  PencilLine,
  PanelLeft as Sidebar,
  Play,
  Plus,
  RefreshCw as Refresh,
  ScanSearch,
  Settings,
  Shield as Policy,
  ShieldCheck as Safety,
  Target as Strategy,
  ScanLine as Visibility,
  Trash2 as Clear,
  TriangleAlert as Attention,
  X,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { SeoStrategy } from "./seo-strategy";

import type { AuthNoticeKey } from "../lib/auth-route";
import type { DashboardOrganizationDirectory } from "../lib/organization-api";
import type {
  DashboardFinding,
  DashboardFindings,
  DashboardPageObservationState,
} from "../lib/finding-api";
import type {
  DashboardProposal,
  DashboardProposalNotice,
  DashboardProposals,
} from "../lib/proposal-api";
import type {
  DashboardCandidateInbox,
  DashboardCandidateRevision,
  CandidateReviewStatus,
} from "../lib/candidate-inbox-api";
import type { DashboardGithubPrOperations, DashboardGithubPrOperation } from "../lib/github-pr-operations-api";
import type { DashboardDeliveryObservations, DashboardDeliveryObservation } from "../lib/github-delivery-api";
import type { DashboardSession } from "../lib/session-api";
import type { DashboardSnapshot, SignalCapability } from "../lib/signal-api";
import type { DashboardSiteDirectory } from "../lib/site-api";
import type { StandingAuthorizationState } from "../lib/standing-authorization-api";
import type { DashboardWork, DashboardWorkResult } from "../lib/work-api";
import type { WeeklyReportState } from "../lib/weekly-report-api";
import { OriginVerification } from "./origin-verification";
import { StandingAuthorization } from "./standing-authorization";
import { SlackConnector } from "./slack-connector";
import { BingConnector, GscConnector, GithubConnector } from "./owner-connectors";
import type { BingState, GscState, GithubState, GithubPrState } from "../lib/owner-connectors-api";
import type { WeeklyPauseState } from "../lib/weekly-loop-api";
import { WeeklyLoopControl } from "./weekly-loop-control";
import { DataForSeoConnector } from "./dataforseo-connector";
import type { DataForSeoState } from "../lib/dataforseo-api";
import { IndexNowConnector } from "./indexnow-connector";
import type { IndexNowState } from "../lib/indexnow-api";
import { Ga4Connector } from "./ga4-connector";
import { EmailPreferences } from "./email-preferences";
import { TeamSettings } from "./team-settings";
import type { TeamState } from "@/lib/team-api";
import { ChatReportPreferences } from "./chat-report-preferences";
import type { ChatReportState } from "../lib/chat-reports-api";

import { HealthPanel } from "./health-panel";
import type { HealthState } from "../lib/health-api";
import type { EmailState } from "../lib/email-api";
import { DocsConnector } from "./docs-connector";
import type { DocsState } from "../lib/docs-api";
import { SlackRequest } from "./slack-request";
import type { SlackState } from "../lib/slack-api";
import type { TelegramState } from "../lib/telegram-api";
import { TelegramConnector } from "./telegram-connector";
import { TelegramRequest } from "./telegram-request";
import { BrandDocuments } from "./brand-documents";
import { BusinessBrain } from "./business-brain";
import { PageSpeed } from "./pagespeed";
import { ContentWriter } from "./content-writer";
import { WordPressDelivery } from "./wordpress-delivery";
import { WebflowInbox } from "./webflow-inbox";
import { WebflowConnection } from "./webflow-connection";

import { AiVisibility } from "./ai-visibility";
import { WorkRefresh } from "./work-refresh";
import { WeeklyReport } from "./weekly-report";
import { HEALTH_LABELS, SetupChecklist, readableCode } from "./home-panels";
import { HomeParity, HomeParityHeader, waitingItems, type WaitingItem } from "./home-parity";
import { NO_INSIGHTS, type OwnerInsights } from "../lib/owner-insights";
import { serpCrumb, serpPreview } from "../lib/serp";
import { ArticleReview, FactReview } from "./inbox-items";
import { ActivityHeader, ActivityLog } from "./activity-log";
import { DecisionTicker } from "./home-insights";
import { AskSignal, AskSignalPage } from "./ask-signal";
import { NO_ASSISTANT, type AssistantPage } from "../lib/assistant-api";
import { ResultsHeader, ResultsParity } from "./results-parity";
import { ContentBoard, ContentHeader } from "./content-board";
import { ConnectionTile, ConnectionTileGroup, ConnectionsHeader, summaries } from "./connections-parity";
import type { ChangeMeasurement } from "../lib/change-measurement-api";
import { buildActivity, type ActivityKind } from "../lib/insights";

export interface InboxSelection { article?: string; fact?: string; kind?: "fix" | "article" | "fact" }
import { VisibilitySchedule } from "./visibility-schedule";

export const dashboardSections = [
  "overview",
  "chat",
  "work",
  "strategy",
  "topics",
  "business-brain",
  "content-writer",
  "pages",
  "changes",
  "approvals",
  "analytics",
  "visibility",
  "recipes",
  "policy",
  "connectors",
  "usage",
  "settings",
  "help",
] as const;

export type DashboardSection = (typeof dashboardSections)[number];

interface DashboardViewProps {
  snapshot: DashboardSnapshot;
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  organizations: DashboardOrganizationDirectory;
  authNotice: AuthNoticeKey | null;
  work?: DashboardWork;
  findings?: DashboardFindings;
  pageObservation?: DashboardPageObservationState;
  proposals?: DashboardProposals;
  standingAuthorization?: StandingAuthorizationState;
  slack?: SlackState;
  gsc?: GscState;
  github?: GithubState;
  githubPr?: GithubPrState;
  weeklyPause?: WeeklyPauseState;
  bing?: BingState;
  dataforseo?: DataForSeoState;
  email?: EmailState;
  team?: TeamState;
  chatReports?: ChatReportState;

  health?: HealthState;
  telegram?: TelegramState;
  docs?: DocsState;
  candidateInbox?: DashboardCandidateInbox;
  githubPrOperations?: DashboardGithubPrOperations;
  deliveryObservations?: DashboardDeliveryObservations;
  indexnow?: IndexNowState;
  weeklyReport?: WeeklyReportState;
  insights?: OwnerInsights;
  assistant?: AssistantPage;
  measurementHistory?: readonly ChangeMeasurement[];
  localPilot?: boolean;
  localVerifiedCrawl?: boolean;
  proposalNotice?: DashboardProposalNotice | null;
  proposalDecisionId?: string;
  candidateDecisionId?: string;
  selectedCandidateRevisionId?: string;
  inboxSelection?: InboxSelection;
  activityFilter?: "all" | ActivityKind;
  siteOnboardingRequestId?: string;
  activeSection?: DashboardSection;
}

interface NavigationItem {
  section: DashboardSection;
  label: string;
  href: string;
}

interface NavigationGroup {
  label: string;
  items: NavigationItem[];
}

interface SectionDefinition {
  title: string;
  description: string;
  ledgerTitle: string;
  ledgerDescription: string;
  rows: ReadonlyArray<{ label: string; source: string; state: string }>;
}

/* Navigation lists only destinations that hold real product surfaces. Recipes, Usage
   and Help are readiness pages without their own data yet: they stay reachable by URL
   (Help from the account menu) but do not compete for attention in the sidebar. */
function navigationGroups(localPilot: boolean): NavigationGroup[] {
  return [
    {
      label: "Work",
      items: [
        { section: "overview", label: "Home", href: "/" },
        { section: "approvals", label: "Inbox", href: "/approvals" },
        { section: "changes", label: "Activity", href: "/changes" },
        { section: "content-writer", label: "Content", href: "/content-writer" },
        ...(localPilot
          ? [
              { section: "work" as const, label: "Site audit", href: "/work" },
              { section: "chat" as const, label: "Ask Signal", href: "/chat" },
            ]
          : []),
      ],
    },
    { label: "Growth", items: [{ section: "analytics", label: "Results", href: "/analytics" }] },
    {
      label: "Setup",
      items: [
        { section: "connectors", label: "Connections", href: "/connectors" },
        { section: "policy", label: "Autonomy", href: "/policy" },
      ],
    },
  ];
}

/** Pages reached through a section's tabs light up that section in the navigation. */
const NAV_FAMILY: Partial<Record<DashboardSection, DashboardSection>> = {
  strategy: "content-writer", topics: "content-writer", "business-brain": "content-writer",
  visibility: "analytics", pages: "analytics",
};

const SUB_TABS: Partial<Record<DashboardSection, { section: DashboardSection; label: string; href: string }[]>> = {
  "content-writer": [
    { section: "content-writer", label: "Board", href: "/content-writer" },
    { section: "strategy", label: "Strategy", href: "/strategy" },
    { section: "topics", label: "Keyword ideas", href: "/topics" },
    { section: "business-brain", label: "Business facts", href: "/business-brain" },
  ],
  analytics: [
    { section: "analytics", label: "Overview", href: "/analytics" },
    { section: "visibility", label: "AI answers", href: "/visibility" },
    { section: "pages", label: "Pages", href: "/pages" },
  ],
};

function SubTabs({ activeSection }: { activeSection: DashboardSection }) {
  const family = NAV_FAMILY[activeSection] ?? activeSection;
  const tabs = SUB_TABS[family];
  if (!tabs) return null;
  return (
    <nav className="c-subtabs" aria-label="Sections in this area">
      {tabs.map((tab) => (
        <a key={tab.section} href={tab.href} className={tab.section === activeSection ? "on" : undefined} aria-current={tab.section === activeSection ? "page" : undefined}>{tab.label}</a>
      ))}
    </nav>
  );
}

const sectionDefinitions: Record<
  Exclude<DashboardSection, "overview">,
  SectionDefinition
> = {
  "business-brain": defineSection("Business facts", "The facts Signal may use in your content, where each one came from, and your brand voice.", "Business facts", "Select an owner-authorized site to review business facts.", [["Owner access", "Current site authorization", "Required"]]),
  "content-writer": defineSection("Articles", "Briefs, drafts, and the articles waiting for your review.", "Content", "Select an owner-authorized site.", [["Owner access", "Current site authorization", "Required"]]),
  chat: defineSection(
    "Ask Signal",
    "Ask about your site. Signal answers only from its records and never acts from a chat.",
    "Conversation",
    "Conversations and memories are kept per site and per person.",
    [
      ["A signed-in person", "Each conversation belongs to one person", "Required"],
      ["A selected site", "Answers come only from that site’s own records", "Required"],
      ["A language model for the site", "Without one, Ask Signal says so and stays closed", "Required"],
    ],
  ),
  work: defineSection(
    "Site audit",
    "Run a site audit and follow it from request to committed result.",
    "Work queue",
    "No task read model is available in this build.",
    [],
  ),
  topics: defineSection(
    "Keyword ideas", "Search queries, gaps and content ideas from the evidence Signal has observed.",
    "Query evidence", "Topics require owner access and query-dimensional imports.",
    [["Search queries", "Current GSC and Bing imports", "Unavailable"]],
  ),
  strategy: defineSection(
    "Strategy",
    "What Signal plans to work on next, ranked against your goals and the evidence.",
    "Opportunities",
    "No qualified research or prioritization projection is connected.",
    [
      ["Business goals", "Approved business facts", "Visible to the owner"],
      [
        "Competitor research",
        "Bounded public evidence",
        "Visible to the owner",
      ],
      ["Prioritized plan", "Coordinator decision", "Visible to the owner"],
    ],
  ),
  pages: defineSection(
    "Pages",
    "What Signal has observed on your pages, with the evidence behind every finding.",
    "Page inventory",
    "No verified-origin crawl inventory is available.",
    [],
  ),
  changes: defineSection(
    "Activity",
    "What Signal did, what allowed it to, and what happened afterwards.",
    "Change ledger",
    "No sealed candidate or external operation is available.",
    [
      ["Sealed revisions", "Candidate manifests", "Visible to the owner"],
      [
        "GitHub pull requests",
        "PR-only connector",
        "No pull request evidence shown",
      ],
      ["Live verification", "Bound public origin", "Visible to the owner"],
    ],
  ),
  approvals: defineSection(
    "Inbox",
    "Decisions only you can make. Each one shows what changes, why, and how to undo it.",
    "Approval inbox",
    "No immutable candidate is requesting authority.",
    [
      ["Eligible revisions", "Sealed change manifests", "Visible to the owner"],
      [
        "Policy decision",
        "Current signed policy",
        "Not available",
      ],
      ["Recovery preview", "Exact revision recovery plan", "Visible to the owner"],
    ],
  ),
  analytics: defineSection(
    "Search results",
    "How your pages perform in search, from each connected source, compared like for like.",
    "Search performance",
    "Charts remain empty until an exact Search Console property is connected and imported.",
    [
      [
        "Search Console",
        "Exact property binding",
        "Visible to the owner",
      ],
      ["Import generation", "Committed source data", "Visible to the owner"],
      [
        "Comparison window",
        "Compatible evidence grain",
        "Unavailable",
      ],
    ],
  ),
  visibility: defineSection(
    "AI answers",
    "Whether AI assistants cite your site for the questions your buyers ask, with the evidence for each answer.",
    "Citation baseline",
    "No visibility observation is projected until an immutable target-question version and provider evidence exist.",
    [
      ["Verified site", "Ownership proof", "Required"],
      ["Target questions", "Versioned crawl and owner sources", "Visible to the owner"],
      ["OpenAI", "Official API evidence", "Visible to the owner"],
      ["Perplexity", "Official API evidence", "Visible to the owner"],
      ["Gemini", "Official API evidence", "Visible to the owner"],
    ],
  ),
  recipes: defineSection(
    "Recipes",
    "Versioned change contracts with compatibility, limits, tests, and recovery behavior.",
    "Recipe catalog",
    "No repository recipe is certified for this site.",
    [
      [
        "Repository stack",
        "Read-only repository snapshot",
        "Unknown",
      ],
      ["Certified release", "Signed qualification", "Visible to the owner"],
      [
        "Recovery contract",
        "Recovery PR execution",
        "Unavailable",
      ],
    ],
  ),
  policy: defineSection(
    "Autonomy",
    "How much Signal does on its own, and the limits it always works within.",
    "Policy bundles",
    "No signed policy bundle is projected to this dashboard.",
    [
      [
        "Applicable bundles",
        "Signed policy releases",
        "Not connected",
      ],
      ["Source freshness", "Authoritative references", "Unknown"],
      ["Hard prohibitions", "Deterministic policy", "Unavailable"],
    ],
  ),
  connectors: defineSection(
    "Connections",
    "The tools Signal reads from or writes to, each with the narrowest access that does the job.",
    "Connection readiness",
    "Resources remain disconnected until each real provider contract is qualified.",
    [],
  ),
  usage: defineSection(
    "Usage",
    "Actual spend, reservations, uncertain costs, quotas, and recovery headroom.",
    "Usage ledger",
    "No attributed model, research, crawl, or build costs are projected.",
    [
      [
        "Actual spend",
        "Settled operation costs",
        "Visible to the owner",
      ],
      ["Reservations", "Admitted work budgets", "Visible to the owner"],
      ["Recovery headroom", "Protected budget", "Visible to the owner"],
    ],
  ),
  settings: defineSection(
    "Settings",
    "Your site details, documents, notifications, health checks and system status.",
    "Current configuration",
    "Only server-verified identity and site metadata are currently readable.",
    [],
  ),
  help: defineSection(
    "Help",
    "Inspect safe diagnostics and get support without exposing sensitive data.",
    "System diagnostics",
    "Current public readiness is shown without internal errors or credentials.",
    [
      ["Control plane", "Public readiness", "Read below"],
      ["Support bundle", "Redacted diagnostics", "Not implemented"],
      [
        "Support access",
        "Scoped temporary grant",
        "No active grant",
      ],
    ],
  ),
};

export function DashboardView({
  snapshot,
  session,
  sites,
  organizations,
  authNotice,
  work = { state: "no_work" },
  findings = { state: "available", findings: [] },
  pageObservation = { state: "available", observation: null },
  proposals = { state: "available", proposals: [] },
  standingAuthorization = { state: "rejected" },
  slack = { availability: "unavailable" },
  gsc = { availability: "unavailable" },
  github = { availability: "unavailable" },
  githubPr = { availability: "unavailable" },
  weeklyPause = { state: "unavailable" },
  bing = { availability: "unavailable" },
  dataforseo = { availability: "unavailable" },
  email = { availability: "unavailable" },
  team = { state: "unavailable" },
  chatReports = { availability: "unavailable" },
  telegram = { availability: "unavailable" },
  docs = { availability: "unavailable", sources: [] },
  candidateInbox = { state: "available", revisions: [] },
  githubPrOperations = { state: "available", operations: [] },
  deliveryObservations = { state: "available", observations: [] },
  indexnow = { state: "unavailable" },
  weeklyReport = { state: "rejected" },
  insights = NO_INSIGHTS,
  assistant = NO_ASSISTANT,
  measurementHistory = [],
  health = { state: "unavailable" },
  localPilot = false,
  localVerifiedCrawl = false,
  proposalNotice = null,
  proposalDecisionId,
  candidateDecisionId,
  selectedCandidateRevisionId,
  inboxSelection = {},
  activityFilter = "all",
  siteOnboardingRequestId,
  activeSection = "overview",
}: DashboardViewProps) {
  const groups = navigationGroups(localPilot);
  const waiting = waitingItems(candidateInbox, insights);
  const pendingDecisions = waiting.length;
  const inboxParity = selectedSite(session, sites) !== null && candidateInbox.state === "available" &&
    (candidateInbox.revisions.length > 0 || waiting.length > 0 || !localPilot);
  const title = activeSection === "overview" ? "Home" : sectionDefinitions[activeSection].title;
  const ownerSite =
    session.state === "authenticated" && session.role === "owner" && session.activeSiteId
      ? session.activeSiteId
      : null;

  return (
    <div className={pendingDecisions > 0 && session.state === "authenticated" ? "app-shell has-ticker" : "app-shell"}>
      {pendingDecisions > 0 && session.state === "authenticated" ? <DecisionTicker count={pendingDecisions} /> : null}
      <TopBar
        snapshot={snapshot}
        session={session}
        sites={sites}
        organizations={organizations}
        work={work}
        weeklyReport={weeklyReport}
        groups={groups}
        activeSection={activeSection}
        pendingDecisions={pendingDecisions}
        title={title}
      />
      <aside className="sidebar" aria-label="Primary navigation">
        <Navigation groups={groups} activeSection={activeSection} pendingDecisions={pendingDecisions} />
        <AutonomyCard session={session} standingAuthorization={standingAuthorization} />
      </aside>
      <TabBar activeSection={activeSection} pendingDecisions={pendingDecisions} />

      <main className="main-area" id="main">
        {activeSection === "overview" ? (
          selectedSite(session, sites) !== null && session.state === "authenticated" ? (
            <HomeParityHeader fetchedAt={snapshot.fetchedAt} weeklyReport={weeklyReport} insights={insights} waiting={pendingDecisions} />
          ) : (
            <HomeHeader snapshot={snapshot} session={session} sites={sites} pendingDecisions={pendingDecisions} />
          )
        ) : activeSection === "changes" && selectedSite(session, sites) !== null ? (
          <ActivityHeader filter={activityFilter} />
        ) : activeSection === "analytics" && ownerSite ? (
          <ResultsHeader />
        ) : activeSection === "connectors" ? (
          <ConnectionsHeader />
        ) : activeSection === "policy" && ownerSite ? (
          <header className="c-ph">
            <div>
              <span className="c-ml">Autonomy · standing approval</span>
              <h1 className="c-h1"><span className="c-ln"><span>How much Signal</span></span><span className="c-ln"><span className="c-soft" style={{ animationDelay: ".1s" }}>does on its own.</span></span></h1>
            </div>
            <span className="c-ph-note">Lowering it takes effect at once. Raising it needs a fresh 2-step verification check.</span>
          </header>
        ) : activeSection === "content-writer" && ownerSite ? (
          <ContentHeader writer={insights.writer.state === "available" ? insights.writer.value : null} />
        ) : (activeSection === "approvals" && inboxParity) || (activeSection === "chat" && selectedSite(session, sites) !== null) ? null : (
          <PageHeader
            title={sectionDefinitions[activeSection].title}
            description={sectionDefinitions[activeSection].description}
          />
        )}
        <SubTabs activeSection={activeSection} />
        {authNotice === null ? null : <AuthNotice notice={authNotice} />}
        {activeSection === "overview" ? (
          <HomeContent
            snapshot={snapshot}
            session={session}
            sites={sites}
            organizations={organizations}
            work={work}
            candidateInbox={candidateInbox}
            githubPrOperations={githubPrOperations}
            deliveryObservations={deliveryObservations}
            weeklyReport={weeklyReport}
            weeklyPause={weeklyPause}
            health={health}
            siteOnboardingRequestId={siteOnboardingRequestId}
            setup={{ gsc, github, githubPr, slack, telegram, standing: standingAuthorization }}
            insights={insights}
            waiting={waiting}
          />
        ) : activeSection === "chat" ? (
          <ChatContent
            session={session}
            sites={sites}
            findings={findings}
            proposals={proposals}
            telegram={telegram}
            localPilot={localPilot}
            notice={proposalNotice}
            assistant={assistant}
          />
        ) : activeSection === "work" ? (
          <WorkContent
            work={work}
            session={session}
            sites={sites}
            localPilot={localPilot}
            localVerifiedCrawl={localVerifiedCrawl}
          />
        ) : activeSection === "pages" ? (
          <>
            {ownerSite && <SeoStrategy key={ownerSite} siteId={ownerSite} view="pages" />}
            <PagesContent
              work={work}
              findings={findings}
              pageObservation={pageObservation}
              session={session}
              sites={sites}
              localPilot={localPilot}
              localVerifiedCrawl={localVerifiedCrawl}
            />
            {ownerSite ? <PageSpeed key={ownerSite} siteId={ownerSite} /> : null}
          </>
        ) : activeSection === "analytics" && ownerSite ? (
          <>
            <ResultsParity insights={insights} candidateInbox={candidateInbox} githubPrOperations={githubPrOperations}
              deliveryObservations={deliveryObservations} measurements={measurementHistory} />
            <div className="c-details-head"><h2>Search details</h2></div>
            <SeoStrategy key={ownerSite} siteId={ownerSite} view="analytics" initialData={insights.seo.state === "available" ? insights.seo.value : undefined} />
          </>
        ) : (activeSection === "strategy" || activeSection === "topics") && ownerSite ? (
          <SeoStrategy key={ownerSite} siteId={ownerSite} view={activeSection} />
        ) : activeSection === "business-brain" && ownerSite ? (
          <BusinessBrain key={ownerSite} siteId={ownerSite} />
        ) : activeSection === "content-writer" && ownerSite ? (
          <>
            <ContentBoard insights={insights} githubPrOperations={githubPrOperations} deliveryObservations={deliveryObservations} />
            <div className="c-details-head"><h2>Briefs and drafts</h2></div>
            <ContentWriter key={ownerSite} siteId={ownerSite} initialData={insights.writer.state === "available" ? insights.writer.value : undefined} />
          </>
        ) : activeSection === "visibility" ? (
          ownerSite ? (
            <><AiVisibility key={ownerSite} siteId={ownerSite} /><VisibilitySchedule key={`schedule-${ownerSite}`} siteId={ownerSite} /></>
          ) : (
            <VisibilityContent session={session} sites={sites} />
          )
        ) : activeSection === "changes" ? (
          <>
            {selectedSite(session, sites) !== null ? (
              <>
                <ActivityLog
                  entries={buildActivity({
                    projection: null,
                    writer: insights.writer.state === "available" ? insights.writer.value : null,
                    revisions: candidateInbox.state === "available" ? candidateInbox.revisions : [],
                    operations: githubPrOperations.state === "available" ? githubPrOperations.operations : [],
                    observations: deliveryObservations.state === "available" ? deliveryObservations.observations : [],
                    measurements: weeklyReport.state === "available" ? weeklyReport.report.measurements : [],
                    report: weeklyReport.state === "available" ? weeklyReport.report : null,
                  })}
                  filter={activityFilter}
                  today={snapshot.fetchedAt}
                />
                <div className="c-details-head">
                  <h2>Delivery details</h2>
                  <p className="page-note">
                    A human-approved draft is not a repository candidate, pull request, deployment, or measured
                    SEO outcome. Each later state needs its own committed evidence.
                  </p>
                </div>
              </>
            ) : null}
            <WeeklyReport value={weeklyReport} />
            <ChangesContent
              session={session}
              sites={sites}
              proposals={proposals}
              candidateInbox={candidateInbox}
              githubPrOperations={githubPrOperations}
              deliveryObservations={deliveryObservations}
              localPilot={localPilot}
              notice={proposalNotice}
            />
          </>
        ) : activeSection === "approvals" ? (
          <>
            <ApprovalsContent
              session={session}
              sites={sites}
              proposals={proposals}
              candidateInbox={candidateInbox}
              localPilot={localPilot}
              notice={proposalNotice}
              decisionId={proposalDecisionId}
              candidateDecisionId={candidateDecisionId}
              slack={slack}
              selectedCandidateRevisionId={selectedCandidateRevisionId}
              telegram={telegram}
              insights={insights}
              selection={inboxSelection}
            />
            {ownerSite && <WordPressDelivery key={`wordpress-${ownerSite}`} siteId={ownerSite} inbox />}
            {ownerSite && <WebflowInbox key={`webflow-${ownerSite}`} siteId={ownerSite} />}
          </>
        ) : activeSection === "connectors" ? (
          <>
            <ConnectionTileGroup title="Essential" detail="Signal can’t work without these">
              <ConnectionTile mono="GSC" name="Google Search Console" desc="Clicks, impressions and pages. Read-only." summary={summaries.gsc(gsc)}>
                <GscConnector state={gsc} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              <ConnectionTile mono="GIT" name="GitHub" desc="Opens pull requests. Never merges or deploys." summary={summaries.github(github)}>
                <GithubConnector state={github} prState={githubPr} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
            </ConnectionTileGroup>
            <ConnectionTileGroup title="Recommended" detail="Better data, faster decisions">
              <ConnectionTile mono="GA4" name="Google Analytics 4" desc="Sign-ups and conversions from search." summary={null}>
                <Ga4Connector key={session.state === "authenticated" ? session.activeSiteId : "none"} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              <ConnectionTile mono="SLK" name="Slack" desc="Weekly reports and approvals." summary={summaries.slack(slack)}>
                <SlackConnector state={slack} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              <ConnectionTile mono="BNG" name="Bing Webmaster Tools" desc="Bing clicks, impressions and top pages. Read-only." summary={summaries.bing(bing)}>
                <BingConnector state={bing} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              <ConnectionTile mono="TG" name="Telegram" desc="Approve changes from your phone." summary={summaries.telegram(telegram)}>
                <TelegramConnector state={telegram} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
            </ConnectionTileGroup>
            <ConnectionTileGroup title="Optional" detail="Turn on when you need them">
              <ConnectionTile mono="DFS" name="DataForSEO" desc="Search volumes and competitor gaps." summary={summaries.dataforseo(dataforseo)}>
                <DataForSeoConnector state={dataforseo} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              <ConnectionTile mono="DOC" name="Google Docs" desc="Your product and brand documents." summary={summaries.docs(docs)}>
                <DocsConnector state={docs} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              {ownerSite ? (
                <ConnectionTile mono="WP" name="WordPress" desc="Send article drafts to WordPress." summary={null}>
                  <WordPressDelivery key={ownerSite} siteId={ownerSite} />
                </ConnectionTile>
              ) : null}
              {ownerSite ? (
                <ConnectionTile mono="WF" name="Webflow" desc="Send approved articles to your Webflow CMS." summary={null}>
                  <WebflowConnection key={`webflow-${ownerSite}`} siteId={ownerSite} />
                </ConnectionTile>
              ) : null}
              <ConnectionTile mono="IDX" name="IndexNow" desc="Tells Bing and others about changed pages." summary={summaries.indexnow(indexnow)}>
                <IndexNowConnector state={indexnow} siteId={session.state === "authenticated" ? session.activeSiteId : null} owner={session.state === "authenticated" && session.role === "owner"} />
              </ConnectionTile>
              <ConnectionTile mono="NOT" name="Notion" desc="Product notes from Notion." summary={{ detail: "Not available yet", status: "Unavailable" }} />
            </ConnectionTileGroup>
          </>
        ) : activeSection === "settings" ? (
          <>
            <SettingsContent session={session} sites={sites} />
            <SiteDirectoryPanel session={session} directory={sites} onboardingRequestId={siteOnboardingRequestId} />
            {ownerSite && <TeamSettings key={ownerSite} state={team} siteId={ownerSite} />}
            <EmailPreferences state={email} siteId={session.state === "authenticated" ? session.activeSiteId : null} />
            {session.state === "authenticated" && session.role === "owner" && (
              <ChatReportPreferences state={chatReports} siteId={session.activeSiteId} />
            )}
            {session.state === "authenticated" && session.role === "owner" ? <HealthPanel value={health} /> : null}
            <SystemSection snapshot={snapshot} />
          </>
        ) : activeSection === "policy" && session.state === "authenticated" && session.role === "owner" ? (
          <>
            <StandingAuthorization
              siteId={session.activeSiteId}
              siteName={sites.state === "available" ?
                sites.sites.find((site) => site.id === session.activeSiteId)?.name ?? null : null}
              state={standingAuthorization}
              verified={sites.state === "available" &&
                sites.sites.some((site) => site.id === session.activeSiteId &&
                  site.ownershipStatus === "verified")}
            />
            {ownerSite ? (
              <section className="c-pn c-weekly-policy" aria-labelledby="weekly-policy-title">
                <div className="c-pn-h"><h2 id="weekly-policy-title">Weekly loop</h2></div>
                <div className="c-loop-control"><WeeklyLoopControl key={ownerSite} siteId={ownerSite} state={weeklyPause} /></div>
              </section>
            ) : null}
          </>
        ) : (
          <UnavailableSection definition={sectionDefinitions[activeSection]} />
        )}
      </main>
    </div>
  );
}

function TopBar({
  snapshot,
  session,
  sites,
  organizations,
  work,
  weeklyReport,
  groups,
  activeSection,
  pendingDecisions,
  title,
}: {
  snapshot: DashboardSnapshot;
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  organizations: DashboardOrganizationDirectory;
  work: DashboardWork;
  weeklyReport: WeeklyReportState;
  groups: NavigationGroup[];
  activeSection: DashboardSection;
  pendingDecisions: number;
  title: string;
}) {
  const current = work.state === "available" ? work.work : null;
  const askSite = selectedSite(session, sites);
  const working =
    (current !== null && ["accepted", "workflow_admitted", "processing"].includes(current.status)) ||
    (weeklyReport.state === "available" && weeklyReport.report.status === "running");
  return (
    <header className="topbar">
      <details className="mobile-navigation">
        <summary aria-label="Open navigation">
          <Sidebar size={18} aria-hidden="true" />
        </summary>
        <div className="mobile-navigation-panel">
          <WorkspaceContext session={session} sites={sites} />
          <Navigation groups={groups} activeSection={activeSection} pendingDecisions={pendingDecisions} />
        </div>
      </details>
      <Brand />
      <WorkspaceContext session={session} sites={sites} />
      <span className="topbar-title">{title}</span>
      {askSite ? <AskSignal siteId={askSite.id} siteName={askSite.name} /> : null}
      <div className="topbar-actions">
        <span className="environment-state" title={snapshot.productionWritesEnabled ? "External writes on" : "Production writes off"}>
          <Safety size={15} aria-hidden="true" />
          {snapshot.releaseStatus === "development" ? "Development" : titleCase(snapshot.releaseStatus)}
          <span aria-hidden="true">·</span>
          {snapshot.productionWritesEnabled ? "External writes on" : "External writes off"}
        </span>
        {session.state === "authenticated" ? (
          <span className={`run-state${working ? " live" : ""}`}>
            <span className="live-dot" aria-hidden="true" />
            {working ? "Working" : "Idle"}
          </span>
        ) : null}
        <AccountMenu session={session} organizations={organizations} />
      </div>
    </header>
  );
}

function AccountMenu({
  session,
  organizations,
}: {
  session: DashboardSession;
  organizations: DashboardOrganizationDirectory;
}) {
  if (session.state !== "authenticated") {
    return <AccountState session={session} organizations={organizations} />;
  }
  const role = titleCase(session.role);
  return (
    <details className="account-menu">
      <summary className="account-state authenticated" aria-label={`Account menu, ${sessionLabel(session)}`}>
        <span className="account-avatar" aria-hidden="true">{role.charAt(0)}</span>
        <span>{sessionLabel(session)}</span>
      </summary>
      <div className="account-panel">
        <p className="account-who">
          <strong>{role}</strong>
          <small>
            {session.authenticationLevel === "mfa"
              ? "Signed in with 2-step verification"
              : "Signed in without 2-step verification"}
          </small>
        </p>
        <p className="account-authority">Session verified · production authority: none</p>
        <a href="/settings">Settings</a>
        <a href="/help">Help</a>
        <form className="account-form" action="/auth/logout" method="post">
          <button type="submit" className="account-signout">
            <SignOut size={16} aria-hidden="true" /> Sign out
          </button>
        </form>
      </div>
    </details>
  );
}

function AutonomyCard({
  session,
  standingAuthorization,
}: {
  session: DashboardSession;
  standingAuthorization: StandingAuthorizationState;
}) {
  if (session.state !== "authenticated" || session.role !== "owner" || standingAuthorization.state !== "available") {
    return null;
  }
  const grant = standingAuthorization.grant;
  const active = grant.state === "active";
  return (
    <a className="autonomy-card" href="/policy">
      <span className="tile-label">Autonomy</span>
      <strong>{active ? "Ships approved kinds of fixes on its own" : "Asks you before every change"}</strong>
      <small>
        {active
          ? grant.weeklyTotalCap === null
            ? "Within your standing approval"
            : `Up to ${grant.weeklyTotalCap} changes a week`
          : grant.state === "no_grant"
            ? "No standing approval yet"
            : `Standing approval ${readableCode(grant.state).toLowerCase()}`}
      </small>
    </a>
  );
}

function HomeHeader({
  snapshot,
  session,
  sites,
  pendingDecisions,
}: {
  snapshot: DashboardSnapshot;
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  pendingDecisions: number;
}) {
  const active = selectedSite(session, sites);
  if (session.state !== "authenticated") {
    return (
      <header className="page-heading home-heading">
        <div>
          <h1>Your SEO and AI-search employee</h1>
          <p>Sign in to see what Signal did this week and what is waiting on you.</p>
        </div>
      </header>
    );
  }
  return (
    <header className="page-heading home-heading">
      <div>
        <h1>
          {active === null
            ? "Choose a site to get started."
            : pendingDecisions === 0
              ? "Nothing waits on you."
              : `${pendingDecisions} ${pendingDecisions === 1 ? "decision waits" : "decisions wait"} on you.`}
        </h1>
        <p>
          {active === null
            ? "Signal works on one site at a time. Pick one below or add a new one."
            : `Here is what Signal did, what it is doing now, and what it needs from you. Updated ${formatTimestamp(snapshot.fetchedAt)}.`}
        </p>
      </div>
      {active !== null && pendingDecisions > 0 ? (
        <a className="primary-command" href="/approvals">
          Review {pendingDecisions === 1 ? "decision" : "decisions"} <ArrowRight size={16} aria-hidden="true" />
        </a>
      ) : null}
    </header>
  );
}

function HomeContent({
  snapshot,
  session,
  sites,
  organizations,
  work,
  candidateInbox,
  githubPrOperations,
  deliveryObservations,
  weeklyReport,
  weeklyPause,
  health,
  siteOnboardingRequestId,
  setup,
  insights,
  waiting,
}: {
  snapshot: DashboardSnapshot;
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  organizations: DashboardOrganizationDirectory;
  work: DashboardWork;
  candidateInbox: DashboardCandidateInbox;
  githubPrOperations: DashboardGithubPrOperations;
  deliveryObservations: DashboardDeliveryObservations;
  weeklyReport: WeeklyReportState;
  weeklyPause: WeeklyPauseState;
  health: HealthState;
  siteOnboardingRequestId?: string;
  setup: { gsc: GscState; github: GithubState; githubPr: GithubPrState; slack: SlackState; telegram: TelegramState; standing: StandingAuthorizationState };
  insights: OwnerInsights;
  waiting: WaitingItem[];
}) {
  const posture = systemPosture(snapshot);
  const active = selectedSite(session, sites);
  const alert = snapshot.connection !== "connected" ? (
    <div className="system-alert" role="status">
      <Attention size={16} aria-hidden="true" />
      <div>
        <strong>{posture.label}</strong>
        <span>{posture.detail}</span>
      </div>
    </div>
  ) : null;
  if (session.state !== "authenticated") {
    return (
      <>
        {alert}
        <WelcomePanel session={session} organizations={organizations} />
        <OrganizationPanel session={session} directory={organizations} />
      </>
    );
  }
  const owner = session.role === "owner";
  const problems = owner && health.state === "available"
    ? [
        ...health.checks.filter((check) => check.state === "warning" || check.state === "critical")
          .map((check) => `${HEALTH_LABELS[check.check]}: ${readableCode(check.reason)}`),
        ...health.checks.filter((check) => check.state === "unknown").map((check) => `${HEALTH_LABELS[check.check]}: could not be checked`),
      ]
    : [];
  return (
    <>
      {alert}
      {active === null ? null : (
        <>
          {owner ? <SetupChecklist verified={active.ownershipStatus === "verified"} {...setup} /> : null}
          <HomeParity
            insights={insights}
            candidateInbox={candidateInbox}
            githubPrOperations={githubPrOperations}
            deliveryObservations={deliveryObservations}
            weeklyReport={weeklyReport}
            work={work}
            fetchedAt={snapshot.fetchedAt}
            waiting={waiting}
            problems={problems}
            loopControl={owner ? <WeeklyLoopControl key={active.id} siteId={active.id} state={weeklyPause} /> : null}
          />
        </>
      )}
      {active === null || active.ownershipStatus !== "verified" ? (
        <SiteDirectoryPanel
          session={session}
          directory={sites}
          onboardingRequestId={siteOnboardingRequestId}
        />
      ) : null}
    </>
  );
}

function WelcomePanel({
  session,
  organizations,
}: {
  session: DashboardSession;
  organizations: DashboardOrganizationDirectory;
}) {
  const canSignIn = session.state === "signed_out" && organizations.state === "absent";
  const attention = canSignIn ? null : sessionAttention(session, organizations);
  return (
    <section className="welcome-panel" aria-labelledby="welcome-title">
      <h2 id="welcome-title">Signal learns your business, finds what to fix and write, and reports every week.</h2>
      <p>
        Every change arrives as a pull request or draft that you approve. Signal never merges,
        deploys, or deletes anything, and it writes only facts you have confirmed.
      </p>
      {canSignIn ? (
        <form className="account-form" action="/auth/login" method="post">
          <button className="primary-command" type="submit" aria-label="Sign in to Signal">
            <SignIn size={16} aria-hidden="true" /> Sign in
          </button>
        </form>
      ) : attention === null ? null : (
        <div className="identity-alert warning" role="status">
          <Attention size={16} aria-hidden="true" />
          <div>
            <strong>{attention.title}</strong>
            <span>{attention.detail}</span>
          </div>
        </div>
      )}
      <ul className="promise-list">
        <li><strong>You stay in control</strong><span>Signal opens pull requests. You merge and deploy.</span></li>
        <li><strong>Only your facts</strong><span>Anything new it wants to say comes to you first.</span></li>
        <li><strong>Every change logged</strong><span>What shipped, why it was allowed, and what it did.</span></li>
      </ul>
    </section>
  );
}

function SystemSection({ snapshot }: { snapshot: DashboardSnapshot }) {
  const posture = systemPosture(snapshot);
  const internal = snapshot.capabilities.filter((capability) => capability.availability === "internal_only");
  const disabled = snapshot.capabilities.filter((capability) => capability.availability === "disabled");
  return (
    <section className="ledger-section system-section" aria-labelledby="system-title">
      <SectionHeading
        id="system-title"
        title="System status"
        detail="What this Signal installation reported about itself, for support and audits."
        meta={`Release: ${snapshot.releaseStatus}`}
      />
      <div className="status-strip" aria-label="System summary">
        <StatusItem label="Control plane" value={posture.label} tone={posture.tone} />
        <StatusItem
          label="Dependencies"
          value={dependencyLabel(snapshot.dependencies)}
          tone={snapshot.dependencies === "ready" ? "positive" : "warning"}
        />
        <StatusItem
          label="External changes"
          value={snapshot.productionWritesEnabled ? "Enabled" : "Blocked"}
          tone={snapshot.productionWritesEnabled ? "warning" : "neutral"}
        />
        <StatusItem
          label="Capability inventory"
          value={snapshot.inventory === "available" ? `${snapshot.capabilities.length} reported` : "Unavailable"}
          tone={snapshot.inventory === "available" ? "positive" : "warning"}
        />
      </div>
      {snapshot.inventory === "available" ? (
        <details className="capability-details">
          <summary>Capability details</summary>
          <div className="capability-columns">
            <CapabilityGroup title="Internal foundations" capabilities={internal} />
            <CapabilityGroup title="Disabled boundaries" capabilities={disabled} />
          </div>
        </details>
      ) : (
        <div className="inline-empty">
          <Unavailable size={16} aria-hidden="true" />
          Capability inventory could not be verified.
        </div>
      )}
    </section>
  );
}

function WorkContent({
  work,
  session,
  sites,
  localPilot,
  localVerifiedCrawl,
}: {
  work: DashboardWork;
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  localPilot: boolean;
  localVerifiedCrawl: boolean;
}) {
  const active = selectedSite(session, sites);
  const needsVerification =
    localPilot && localVerifiedCrawl && active?.ownershipStatus !== "verified";
  const current = work.state === "available" ? work.work : null;
  const inProgress =
    current !== null &&
    ["accepted", "workflow_admitted", "processing"].includes(current.status);
  const step = current === null ? 0 : workStep(current);
  return (
    <>
      {inProgress ? <WorkRefresh active /> : null}
      <section className="truth-band" aria-label="Work execution summary">
        <StatusItem
          className="truth-cell"
          label="Selected site"
          value={active?.name ?? "Not selected"}
          tone={active === null ? "warning" : "positive"}
        />
        <StatusItem
          className="truth-cell"
          label="Workflow state"
          value={current === null ? "Ready" : workStatusLabel(current.status)}
          tone={current?.status === "failed" ? "warning" : "positive"}
        />
        <StatusItem
          className="truth-cell"
          label="External authority"
          value="No writes"
          tone="neutral"
        />
      </section>

      <section className="work-execution" aria-labelledby="work-execution-title">
        <header className="work-execution-header">
          <div>
            <span className="pill">
              {localVerifiedCrawl ? "Verified-site local crawl" : localPilot ? "Local product walkthrough" : "Site observation"}
            </span>
            <h2 id="work-execution-title">
              {localPilot ? "Run a durable site audit" : "Durable site audit"}
            </h2>
            <p>
              {localPilot
                ? localVerifiedCrawl
                  ? "This disposable pilot crawls one verified origin through the pinned, robots-aware network boundary."
                  : "This walkthrough crosses the real database, outbox, Temporal workflow, and worker without contacting the configured customer origin."
                : "The production crawl executor is not connected in this release."}
            </p>
          </div>
          <form action="/actions/start-snapshot" method="post">
            <input type="hidden" name="site_id" value={active?.id ?? ""} />
            <button
              className="primary-command"
              type="submit"
              disabled={active === null || !localPilot || needsVerification}
            >
              <Play size={16} aria-hidden="true" />
              {!localPilot
                ? "Unavailable"
                : needsVerification
                  ? "Verify origin first"
                  : current === null
                    ? "Start audit"
                    : "Run again"}
            </button>
          </form>
        </header>

        <ol className="work-pipeline" aria-label="Audit execution stages">
          {[
            ["Request", "Human intent recorded"],
            ["Queue", "Outbox admitted"],
            ["Execute", "Temporal worker"],
            ["Result", "Manifest committed"],
          ].map(([label, detail], index) => (
            <li
              className={index < step ? "complete" : index === step ? "current" : "pending"}
              key={label}
            >
              <span className="pipeline-marker">
                {index < step ? (
                  <Verified size={15} aria-hidden="true" />
                ) : (
                  index + 1
                )}
              </span>
              <strong>{label}</strong>
              <small>{detail}</small>
            </li>
          ))}
        </ol>

        {active === null ? (
          <div className="work-empty-state">
            <Attention size={18} aria-hidden="true" />
            <div>
              <strong>Select or create a site first</strong>
              <p>The operation derives its scope from the current server-side session.</p>
            </div>
          </div>
        ) : current !== null ? (
          <WorkResult work={current} syntheticLocal={localPilot && !localVerifiedCrawl} />
        ) : work.state === "invalid" || work.state === "unavailable" ? (
          <div className="work-empty-state">
            <Attention size={18} aria-hidden="true" />
            <div>
              <strong>Work state is temporarily unavailable</strong>
              <p>Refresh after the API and workflow dependencies are ready.</p>
            </div>
          </div>
        ) : (
          <div className="work-empty-state">
            <ScanSearch size={20} aria-hidden="true" />
            <div>
              <strong>No audit has run for this site</strong>
              <p>Start the first operation to exercise the complete durable execution path.</p>
            </div>
          </div>
        )}
      </section>
    </>
  );
}

function WorkResult({
  work,
  syntheticLocal,
}: {
  work: DashboardWorkResult;
  syntheticLocal: boolean;
}) {
  return (
    <div className="work-result">
      <div className="work-result-title">
        <span className={`work-state work-state-${work.status}`}>
          {workStatusLabel(work.status)}
        </span>
        <strong>Site snapshot</strong>
        <code>{work.commandId.slice(0, 8)}</code>
      </div>
      {work.result === null ? (
        <p>
          {work.status === "failed"
            ? "The worker closed the run safely without a result."
            : "The accepted operation is progressing. This page refreshes automatically."}
        </p>
      ) : (
        <dl className="work-evidence-grid">
          <div>
            <dt>Coverage</dt>
            <dd>{work.result.coverage}</dd>
          </div>
          <div>
            <dt>{syntheticLocal ? "Sandbox URLs" : "Discovered URLs"}</dt>
            <dd>{work.result.discoveredCount}</dd>
          </div>
          <div>
            <dt>Terminal URLs</dt>
            <dd>{work.result.terminalCount}</dd>
          </div>
          <div>
            <dt>Manifest</dt>
            <dd>{work.result.manifestId.slice(0, 8)}</dd>
          </div>
        </dl>
      )}
      <div className="work-receipt">
        <span>Accepted {formatTimestamp(work.acceptedAt)}</span>
        <span>{work.workflowId === null ? "Workflow pending" : "Workflow identity recorded"}</span>
        {work.result === null ? null : <a href="/pages">Inspect evidence</a>}
      </div>
    </div>
  );
}

function workStep(work: DashboardWorkResult): number {
  if (work.status === "accepted") return 1;
  if (work.status === "workflow_admitted" || work.status === "processing") return 2;
  return work.status === "succeeded" ? 4 : 3;
}

function workStatusLabel(status: DashboardWorkResult["status"]): string {
  return {
    accepted: "Queued",
    workflow_admitted: "Admitted",
    processing: "Running",
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  }[status];
}

function PagesContent({
  work,
  findings,
  pageObservation,
  session,
  sites,
  localPilot,
  localVerifiedCrawl,
}: {
  work: DashboardWork;
  findings: DashboardFindings;
  pageObservation: DashboardPageObservationState;
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  localPilot: boolean;
  localVerifiedCrawl: boolean;
}) {
  const syntheticLocal = localPilot && !localVerifiedCrawl;
  const active = selectedSite(session, sites);
  const current = work.state === "available" ? work.work : null;
  const result = current?.result ?? null;
  const currentFindings = findings.state === "available" ? findings.findings : [];
  const verifiedFindings = currentFindings.filter(
    (finding) => finding.sourceKind === "verified_origin",
  );
  const observation =
    pageObservation.state === "available" ? pageObservation.observation : null;
  return (
    <>
      <section className="truth-band" aria-label="Evidence summary">
        <StatusItem
          className="truth-cell"
          label="Selected site"
          value={active?.name ?? "Not selected"}
          tone={active === null ? "warning" : "positive"}
        />
        <StatusItem
          className="truth-cell"
          label="Evidence state"
          value={observation === null ? (result === null ? "No manifest" : "Audit ready") : "Homepage observed"}
          tone={result === null ? "neutral" : "positive"}
        />
        <StatusItem
          className="truth-cell"
          label="SEO findings"
          value={
            verifiedFindings.length === 0
              ? "No verified findings"
              : `${verifiedFindings.length} verified finding`
          }
          tone={verifiedFindings.length === 0 ? "neutral" : "warning"}
        />
      </section>

      {result === null || current === null ? (
        <section className="evidence-view" aria-labelledby="evidence-title">
          <div className="work-empty-state">
            <ScanSearch size={20} aria-hidden="true" />
            <div>
              <h2 id="evidence-title">No inspectable audit evidence yet</h2>
              <p>
                Complete a durable audit first. This page will render only its committed
                manifest and provenance.
              </p>
              <a href="/work">Open Work</a>
            </div>
          </div>
        </section>
      ) : (
        <section
          className="evidence-view"
          aria-labelledby="evidence-title"
          data-synthetic-evidence={syntheticLocal && observation === null ? "true" : undefined}
        >
          <header className="evidence-header">
            <div>
              <h2 id="evidence-title">Latest site crawl</h2>
              <p>
                What Signal’s last audit covered. The exact, unchangeable record is under Audit
                manifest.
              </p>
              {syntheticLocal ? <span className="pill pill-warning">Synthetic local evidence</span> : null}
            </div>
            <div className="evidence-actions">
              <span className="work-state work-state-succeeded">Committed</span>
              {localPilot && active?.ownershipStatus === "verified" ? (
                <form action="/actions/analyze-homepage" method="post">
                  <input type="hidden" name="site_id" value={active.id} />
                  <button className="primary-command" type="submit">
                    <ScanSearch size={16} aria-hidden="true" />
                    {observation === null ? "Analyze verified homepage" : "Refresh homepage evidence"}
                  </button>
                </form>
              ) : active !== null ? (
                <a className="secondary-command" href="/#site-directory-title">
                  <Authority size={16} aria-hidden="true" /> Verify origin first
                </a>
              ) : null}
            </div>
          </header>

          <dl className="work-evidence-grid">
            <div>
              <dt>Coverage</dt>
              <dd>{readableCode(result.coverage)}</dd>
            </div>
            <div>
              <dt>{syntheticLocal ? "Sandbox URLs" : "Discovered URLs"}</dt>
              <dd>{result.discoveredCount}</dd>
            </div>
            <div>
              <dt>URLs finished</dt>
              <dd>{result.terminalCount}</dd>
            </div>
            <div>
              <dt>Collected</dt>
              <dd>{formatTimestamp(current.projectedAt ?? current.acceptedAt)}</dd>
            </div>
          </dl>

          <details className="technical-details evidence-technical">
          <summary>Audit manifest</summary>
          <dl className="evidence-provenance">
            <div>
              <dt>Manifest identity</dt>
              <dd>
                <code>{result.manifestId}</code>
              </dd>
            </div>
            <div>
              <dt>Content digest</dt>
              <dd>
                <code>{result.manifestSha256}</code>
              </dd>
            </div>
            <div>
              <dt>Scope release</dt>
              <dd>v{result.scopeVersion}</dd>
            </div>
            <div>
              <dt>Crawl policy release</dt>
              <dd>v{result.crawlPolicyVersion}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{syntheticLocal ? "Local no-network executor" : "Verified crawl workflow"}</dd>
            </div>
          </dl>
          </details>

          {observation === null ? null : (
            <article className="fixture-finding" aria-label="Verified homepage observation">
              <header>
                <div>
                  <h3>{observation.title ?? observation.finalUrl}</h3>
                  <span className="pill pill-positive">Verified origin evidence</span>
                </div>
                <span className="work-state work-state-succeeded">HTTP {observation.httpStatus}</span>
              </header>
              <p>
                {observation.metaDescription ?? "No non-empty meta description was observed."}
              </p>
              <dl className="evidence-provenance">
                <div><dt>Final URL</dt><dd><code>{observation.finalUrl}</code></dd></div>
                <div><dt>First heading</dt><dd>{observation.heading ?? "Not present"}</dd></div>
                <div><dt>Observed</dt><dd>{formatTimestamp(observation.observedAt)}</dd></div>
              </dl>
              <details className="technical-details">
                <summary>Technical details</summary>
                <dl className="evidence-provenance">
                  <div><dt>Evidence identity</dt><dd><code>{observation.evidenceId}</code></dd></div>
                  <div><dt>Body digest</dt><dd><code>{observation.bodySha256}</code></dd></div>
                </dl>
              </details>
            </article>
          )}

          {verifiedFindings.length === 0 ? (
            <div className="finding-empty">
              <Pages size={20} aria-hidden="true" />
              <div>
                <h3>No verified homepage finding is open</h3>
                <p>
                  {observation === null
                    ? "Analyze the verified homepage to create immutable page evidence and deterministic metadata findings."
                    : "The latest verified homepage includes a non-empty meta description."}
                </p>
              </div>
            </div>
          ) : (
            <div className="fixture-findings" aria-label="Verified homepage findings">
              {verifiedFindings.map((finding) => (
                <FindingCard finding={finding} key={finding.findingId} sourceLabel="Verified homepage" />
              ))}
            </div>
          )}

        </section>
      )}
    </>
  );
}

function FindingCard({
  finding,
  sourceLabel,
}: {
  finding: DashboardFinding;
  sourceLabel: string;
}) {
  return (
    <article className="fixture-finding">
      <header>
        <div>
          <span className="pill">Deterministic finding</span>
          <h3>{finding.title}</h3>
        </div>
        <span className="finding-severity">Medium</span>
      </header>
      <p>{finding.summary}</p>
      <dl className="evidence-provenance">
        <div><dt>{sourceLabel}</dt><dd><code>{finding.resourceLocator}</code></dd></div>
        <div><dt>Evidence identity</dt><dd><code>{finding.evidenceId}</code></dd></div>
        <div><dt>Content digest</dt><dd><code>{finding.contentSha256}</code></dd></div>
        <div><dt>Observed</dt><dd>{formatTimestamp(finding.evidenceObservedAt)}</dd></div>
        <div><dt>Confidence</dt><dd>{finding.confidenceClass}</dd></div>
      </dl>
    </article>
  );
}

function defineSection(
  title: string,
  description: string,
  ledgerTitle: string,
  ledgerDescription: string,
  rows: ReadonlyArray<readonly [string, string, string]>,
): SectionDefinition {
  return {
    title,
    description,
    ledgerTitle,
    ledgerDescription,
    rows: rows.map(([label, source, state]) => ({ label, source, state })),
  };
}

function PageHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
    </header>
  );
}

function ChatContent({
  session,
  sites,
  findings,
  proposals,
  telegram,
  localPilot,
  notice,
  assistant,
}: {
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  findings: DashboardFindings;
  proposals: DashboardProposals;
  telegram: TelegramState;
  localPilot: boolean;
  notice: DashboardProposalNotice | null;
  assistant: AssistantPage;
}) {
  const active = selectedSite(session, sites);
  const telegramPaired = telegram.availability === "bound" && telegram.link_id !== null;
  const ask = active === null ? null : (
    <AskSignalPage siteId={active.id} siteName={active.name} overview={assistant.overview} conversation={assistant.conversation}
      memories={assistant.memories} telegramPaired={telegramPaired} />
  );
  const finding = findings.state === "available"
    ? findings.findings.find((item) => item.sourceKind === "verified_origin")
    : undefined;
  const proposal = proposals.state === "available"
    ? proposals.proposals.find(
        (item) => item.proposalKind === "model_verified_homepage_metadata_draft",
      )
    : undefined;
  if (!localPilot) {
    return ask ?? (
      <>
        <section className="conversation-empty" aria-labelledby="conversation-title">
          <h2 id="conversation-title">Choose a site to ask about</h2>
          <p>Ask Signal answers about one site at a time, from that site’s own records.</p>
        </section>
        <ReadinessSection definition={sectionDefinitions.chat} />
      </>
    );
  }
  return (
    <>
      {ask}
      <h2 className="c-subhead">Local walkthrough</h2>
      <section className="truth-band" aria-label="Local conversation state">
        <StatusItem
          className="truth-cell"
          label="Command surface"
          value="Local walkthrough"
          tone="positive"
        />
        <StatusItem
          className="truth-cell"
          label="Evidence"
          value={finding === undefined ? "Finding required" : "Exact source bound"}
          tone={finding === undefined ? "warning" : "positive"}
        />
        <StatusItem
          className="truth-cell"
          label="External authority"
          value="No writes"
          tone="neutral"
        />
      </section>
      {notice === null ? null : <ProposalNotice notice={notice} />}
      <section className="conversation" aria-labelledby="conversation-title">
        <h2 className="sr-only" id="conversation-title">Signal conversation</h2>
        <div className="conversation-message user-message">
          <span className="message-author">You</span>
          <p>Prepare a safe proposal for the current SEO finding.</p>
        </div>
        {finding === undefined ? (
          <div className="conversation-message signal-message">
            <span className="message-author">Signal</span>
            <p>I need one committed finding before I can prepare an evidence-backed proposal.</p>
            <a className="inline-command" href="/pages">
              Open Pages <ArrowRight size={14} aria-hidden="true" />
            </a>
          </div>
        ) : proposal === undefined ? (
          <>
            <div className="conversation-message signal-message">
              <span className="message-author">Signal</span>
              <p>
                I found a missing description on the owner-verified homepage. The bounded
                Luna content role can draft one description from its title and heading, then
                Signal will seal the exact output for your review.
              </p>
              <div className="message-evidence">
                <span><FileCheck2 size={14} aria-hidden="true" /> {finding.title}</span>
                <code>{finding.evidenceId.slice(0, 8)}</code>
              </div>
            </div>
            {active === null ? null : (
              <form className="typed-command" action="/actions/prepare-proposal" method="post">
                <input type="hidden" name="site_id" value={active.id} />
                <div>
                  <span className="command-label">Typed command</span>
                  <strong>Draft with GPT-6 Luna</strong>
                  <p>Strict schema, exact evidence, no tools, no external write</p>
                </div>
                <button className="primary-command" type="submit">
                  Prepare proposal <ArrowRight size={16} aria-hidden="true" />
                </button>
              </form>
            )}
          </>
        ) : (
          <>
            <div className="conversation-message signal-message">
              <span className="message-author">Signal</span>
              <p>
                Revision {proposal.revisionNumber} is sealed from evidence {proposal.evidenceId.slice(0, 8)}.
                {proposal.model === null
                  ? " Four deterministic roles completed their bounded checks."
                  : " GPT-6 Luna drafted the copy and deterministic scope checks passed."}
              </p>
            </div>
            <ProposalSummary proposal={proposal} />
          </>
        )}
      </section>
    </>
  );
}

function ProposalNotice({ notice }: { notice: DashboardProposalNotice }) {
  const details: Record<DashboardProposalNotice, [string, string]> = {
    prepared: ["Proposal prepared", "The exact revision is now in the Approval inbox."],
    approved: ["Approval recorded", "The local draft is accepted. No external operation was dispatched."],
    rejected: ["Rejection recorded", "The immutable decision is stored. No external operation was dispatched."],
    changes_requested: ["Edits requested", "The current revision is closed without external action."],
    request_rejected: ["Request rejected", "The browser request did not satisfy the same-origin contract."],
    not_authenticated: ["Session required", "Sign in and select the exact site before continuing."],
    unavailable: ["Service unavailable", "The proposal boundary could not be reached safely."],
    invalid: ["Response rejected", "Signal did not accept an invalid proposal response."],
    conflict: ["State changed", "Refresh and inspect the current exact revision."],
  };
  const [title, detail] = details[notice];
  return (
    <div className={`proposal-notice proposal-notice-${notice}`} role="status">
      {notice === "prepared" || notice === "approved" ? (
        <Verified size={17} aria-hidden="true" />
      ) : (
        <Attention size={17} aria-hidden="true" />
      )}
      <span><strong>{title}</strong>{detail}</span>
    </div>
  );
}

function ProposalSummary({ proposal }: { proposal: DashboardProposal }) {
  return (
    <article className="proposal-summary" aria-label="Prepared proposal">
      <header>
        <div>
          <span className="command-label">Sealed evidence-bound draft</span>
          <h3>Add a meta description to the verified homepage</h3>
        </div>
        <span className={`approval-state approval-state-${proposal.approvalStatus}`}>
          {approvalStatusLabel(proposal.approvalStatus)}
        </span>
      </header>
      <dl className="proposal-facts">
        <div><dt>Impact</dt><dd>{proposal.impact}</dd></div>
        <div><dt>Risk</dt><dd>Low</dd></div>
        <div><dt>Checks</dt><dd>{proposal.tests.length} passed</dd></div>
        <div><dt>Cost ceiling</dt><dd>{formatMinorUnits(proposal.maximumCostMinorUnits)}</dd></div>
      </dl>
      <div className="proposal-revision">
        <span>Revision {proposal.revisionNumber}</span>
        <code>{proposal.revisionSha256}</code>
      </div>
      <a className="primary-command proposal-link" href="/approvals">
        {proposal.approvalStatus === "pending" ? "Review exact revision" : "View decision"}
        <ArrowRight size={16} aria-hidden="true" />
      </a>
    </article>
  );
}

function ArticleChanges({ operations, observations }: {
  operations: DashboardGithubPrOperations;
  observations: DashboardDeliveryObservations;
}) {
  if (operations.state !== "available") return null;
  const articles = operations.operations.filter(operation => operation.authority?.kind === "owner_editorial");
  if (!articles.length) return null;
  return <section className="work-execution" aria-label="Article changes">
    <h2>Article changes</h2>
    {articles.map(operation => <article className="approval-section change-card" key={operation.operationId}>
      <h3>Owner-reviewed article</h3>
      <p className="change-status">{operationStatus(operation)}</p>
      <dl className="change-facts">
        <div><dt>Approved through</dt><dd>Owner editorial approval · Dashboard</dd></div>
        <div><dt>Pull request</dt><dd>{operation.prUrl ? <a href={operation.prUrl} target="_blank" rel="noopener noreferrer">#{operation.prNumber} on GitHub</a> : "Not opened"}</dd></div>
      </dl>
      <details className="technical-details">
        <summary>Technical details</summary>
        <dl className="change-scope">
          <div><dt>Candidate</dt><dd><code>{operation.revisionId}</code></dd></div>
          <div><dt>Revision</dt><dd><code>{operation.revisionSha256}</code></dd></div>
          <div><dt>Authorization record</dt><dd><code>{operation.authority!.recordId}</code></dd></div>
          <div><dt>Owner</dt><dd><code>{operation.authority!.ownerUserId}</code></dd></div>
        </dl>
      </details>
      {operation.state === "opened" && <DeliveryEvidence
        observation={observations.state === "available" ? observations.observations.find(item => item.operationId === operation.operationId && (item.revisionSha256 === null || item.revisionSha256 === operation.revisionSha256)) : undefined}
        available={observations.state === "available"}
        recoveryPlan="Separate owner-reviewed inverse patch; no deletion or unpublish authority."
      />}
    </article>)}
  </section>;
}

function ChangesContent({
  session,
  sites,
  proposals,
  candidateInbox,
  githubPrOperations,
  deliveryObservations,
  localPilot,
  notice,
}: {
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  proposals: DashboardProposals;
  candidateInbox: DashboardCandidateInbox;
  githubPrOperations: DashboardGithubPrOperations;
  deliveryObservations: DashboardDeliveryObservations;
  localPilot: boolean;
  notice: DashboardProposalNotice | null;
}) {
  const active = selectedSite(session, sites);
  if (active !== null && candidateInbox.state === "available" && candidateInbox.revisions.length > 0) {
    return (
      <><ArticleChanges operations={githubPrOperations} observations={deliveryObservations}/><section className="work-execution" aria-labelledby="candidate-changes-title">
        <header className="work-execution-header">
          <div>
            <h2 id="candidate-changes-title">Technical changes</h2>
            <p>Pull requests are not live delivery. Checks, deployment, and live verification remain separate.</p>
          </div>
        </header>
        {candidateInbox.revisions.map((revision) => {
          const operation = githubPrOperations.state === "available"
            ? githubPrOperations.operations.find((item) => item.revisionId === revision.revisionId && item.revisionSha256 === revision.revisionSha256)
            : undefined;
          return (
            <article id={`operation-${operation?.operationId ?? revision.revisionId}`} className="approval-section technical-change" key={revision.revisionId} aria-label={`Change for ${revision.finding.title}`}>
              <h3>{revision.finding.title}</h3>
              <p>{revision.finding.summary}</p>
              <p className="change-status">{operation ? operationStatus(operation) : `${reviewStatusLabel(revision.reviewStatus)} · no pull request yet`}</p>
              <dl className="change-facts">
                <div><dt>Page</dt><dd><a href={revision.evidence.pageUrl} target="_blank" rel="noopener noreferrer">{revision.evidence.pageUrl}</a></dd></div>
                <div><dt>File</dt><dd><code>{revision.sourcePath}</code></dd></div>
                <div><dt>Your decision</dt><dd>{reviewStatusLabel(revision.reviewStatus)}</dd></div>
                {operation ? <div><dt>Approved through</dt><dd>{operation.authority ? <>
                  {operation.authority.kind === "standing_grant" ? "Standing authorization" : operation.authority.kind === "owner_editorial" ? "Owner editorial approval" : "Owner Inbox approval"}
                  {operation.authority.decisionChannel && <> · Decision channel: {operation.authority.decisionChannel === "slack" ? "Slack" : operation.authority.decisionChannel === "telegram" ? "Telegram" : "Dashboard"}</>}
                </> : "Authorization evidence unavailable"}</dd></div> : null}
                <div><dt>Pull request</dt><dd>{operation?.prUrl ? <a href={operation.prUrl} target="_blank" rel="noopener noreferrer">#{operation.prNumber} on GitHub</a> : "Not opened"}</dd></div>
              </dl>
              <details className="technical-details">
                <summary>Technical details</summary>
                <dl className="change-scope">
                  <div><dt>Revision</dt><dd><code>{revision.revisionSha256}</code></dd></div>
                  <div><dt>Operation</dt><dd>{operation ? <code>{operation.operationId}</code> : githubPrOperations.state === "available" ? "Not recorded" : "Evidence unavailable"}</dd></div>
                  {operation?.authority ? <div><dt>Authorization record</dt><dd><code>{operation.authority.recordId}</code></dd></div> : null}
                  {operation?.authority ? <div><dt>Owner</dt><dd><code>{operation.authority.ownerUserId}</code></dd></div> : null}
                  {operation?.journalBodyHash ? <div><dt>Write intent</dt><dd><code>{operation.journalBodyHash}</code></dd></div> : null}
                  {operation?.expectedTreeSha ? <div><dt>Expected tree</dt><dd><code>{operation.expectedTreeSha}</code></dd></div> : null}
                </dl>
              </details>
              {operation?.state === "opened" ? <DeliveryEvidence
                observation={deliveryObservations.state === "available" ? deliveryObservations.observations.find(item => item.operationId === operation.operationId && (item.revisionSha256 === null || item.revisionSha256 === revision.revisionSha256)) : undefined}
                available={deliveryObservations.state === "available"}
                recoveryPlan={revision.recoveryPlan}
              /> : null}
              {revision.reviewStatus === "approved" && operation === undefined ? <p>Approval records the exact revision. It does not authorize a repository write by itself.</p> : null}
            </article>
          );
        })}
      </section></>
    );
  }
  if(active!==null&&githubPrOperations.state==="available"&&githubPrOperations.operations.some(operation=>operation.authority?.kind==="owner_editorial")) return <ArticleChanges operations={githubPrOperations} observations={deliveryObservations}/>;
  const current = proposals.state === "available"
    ? proposals.proposals.find(
        (item) => item.proposalKind === "model_verified_homepage_metadata_draft",
      )
    : undefined;
  if (!localPilot || current === undefined || active === null) {
    return (
      <>
        {notice === null ? null : <ProposalNotice notice={notice} />}
        <UnavailableSection definition={sectionDefinitions.changes} />
      </>
    );
  }

  const accepted = current.approvalStatus === "approved";
  const pending = current.approvalStatus === "pending";
  const closed = !accepted && !pending;
  const stages = [
    ["Evidence", "Verified origin", "complete"],
    ["Draft", "Luna output sealed", "complete"],
    [
      "Decision",
      accepted ? "Exact draft accepted" : approvalStatusLabel(current.approvalStatus),
      accepted ? "complete" : "current",
    ],
    ["Repository", "Exact target required", accepted ? "current" : "pending"],
    ["Candidate", "Not built", "pending"],
    ["Pull request", "External write blocked", "pending"],
    ["Live check", "Not started", "pending"],
  ] as const;

  return (
    <>
      {notice === null ? null : <ProposalNotice notice={notice} />}
      <section className="truth-band" aria-label="Change delivery summary">
        <StatusItem
          className="truth-cell"
          label="Revision"
          value={`${current.revisionNumber} sealed`}
          tone="positive"
        />
        <StatusItem
          className="truth-cell"
          label="Human decision"
          value={approvalStatusLabel(current.approvalStatus)}
          tone={accepted ? "positive" : pending ? "warning" : "neutral"}
        />
        <StatusItem
          className="truth-cell"
          label="Repository"
          value="Not bound"
          tone={accepted ? "warning" : "neutral"}
        />
        <StatusItem
          className="truth-cell"
          label="External write"
          value="Blocked"
          tone="neutral"
        />
      </section>

      <section className="work-execution" aria-labelledby="change-delivery-title">
        <header className="work-execution-header">
          <div>
            <span className="pill">Exact revision delivery</span>
            <h2 id="change-delivery-title">Verified homepage metadata</h2>
            <p>
              Signal follows the same immutable revision from verified evidence through a
              repository candidate, pull request, deployment observation, and live check.
            </p>
          </div>
          {accepted ? (
            <a className="primary-command" href="/connectors">
              <Connectors size={16} aria-hidden="true" /> Connect repository
            </a>
          ) : pending ? (
            <a className="primary-command" href="/approvals">
              <Approvals size={16} aria-hidden="true" /> Review revision
            </a>
          ) : (
            <a className="secondary-command" href="/chat">
              <Chat size={16} aria-hidden="true" /> Prepare a new revision
            </a>
          )}
        </header>

        <ol className="work-pipeline change-pipeline" aria-label="Change delivery stages">
          {stages.map(([label, detail, state], index) => (
            <li className={state} key={label}>
              <span className="pipeline-marker">
                {state === "complete" ? <Verified size={15} aria-hidden="true" /> : index + 1}
              </span>
              <strong>{label}</strong>
              <small>{detail}</small>
            </li>
          ))}
        </ol>

        <div className="change-delivery-detail">
          <section className="approval-section" aria-labelledby="change-revision-title">
            <h3 id="change-revision-title">Accepted scope</h3>
            <dl className="change-scope">
              <div><dt>Resource</dt><dd><code>{current.resourceLocator}</code></dd></div>
              <div><dt>Field</dt><dd><code>meta_description</code></dd></div>
              <div><dt>Evidence</dt><dd><code>{current.evidenceId}</code></dd></div>
              <div><dt>Revision</dt><dd><code>{current.revisionSha256}</code></dd></div>
            </dl>
            <div className="change-diff" aria-label="Exact accepted metadata difference">
              <div className="diff-before"><span>Before</span><code>Not present</code></div>
              <div className="diff-after"><span>After</span><code>{current.afterValue}</code></div>
            </div>
          </section>
          <section className="approval-section" aria-labelledby="change-blocker-title">
            <h3 id="change-blocker-title">Next required boundary</h3>
            <p>
              {accepted
                ? "Bind one exact GitHub App installation, repository, base branch, and approved content path before Signal can build a candidate."
                : closed
                  ? "This immutable revision is closed. Signal will not build or deliver it."
                  : "Approve this exact revision before any repository candidate can be prepared."}
            </p>
            <ul className="check-list">
              <li><Safety size={14} aria-hidden="true" /> No branch, commit, or pull request exists</li>
              <li><Safety size={14} aria-hidden="true" /> Base commit and content path are not inferred</li>
              <li><Safety size={14} aria-hidden="true" /> Deployment and live verification remain unclaimed</li>
            </ul>
          </section>
        </div>
      </section>
    </>
  );
}

function ApprovalsContent({
  session,
  sites,
  proposals,
  candidateInbox,
  localPilot,
  notice,
  decisionId,
  candidateDecisionId,
  slack,
  selectedCandidateRevisionId,
  telegram,
  insights,
  selection,
}: {
  session: DashboardSession;
  sites: DashboardSiteDirectory;
  proposals: DashboardProposals;
  candidateInbox: DashboardCandidateInbox;
  localPilot: boolean;
  notice: DashboardProposalNotice | null;
  decisionId?: string;
  candidateDecisionId?: string;
  slack: SlackState;
  selectedCandidateRevisionId?: string;
  telegram: TelegramState;
  insights: OwnerInsights;
  selection: InboxSelection;
}) {
  const active = selectedSite(session, sites);
  const otherWaiting = (insights.writer.state === "available" && insights.writer.value.candidates.some((candidate) => candidate.review_status === "pending" || (candidate.review_status === "approved" && !candidate.delivery_approval_id)))
    || (insights.facts.state === "available" && insights.facts.value.some((fact) => fact.status === "proposed"));
  if (active !== null && candidateInbox.state === "available" && (candidateInbox.revisions.length > 0 || otherWaiting || !localPilot)) {
    return <CandidateInboxContent revisions={candidateInbox.revisions} siteId={active.id} decisionId={candidateDecisionId} slack={slack} telegram={telegram} selectedRevisionId={selectedCandidateRevisionId} insights={insights} selection={selection} />;
  }
  if (active !== null && candidateInbox.state !== "available") {
    return <CandidateInboxUnavailable state={candidateInbox.state} />;
  }
  const current = proposals.state === "available"
    ? proposals.proposals.find(
        (item) => item.proposalKind === "model_verified_homepage_metadata_draft",
      )
    : undefined;
  if (!localPilot || current === undefined || active === null) {
    return (
      <>
        {notice === null ? null : <ProposalNotice notice={notice} />}
        <section className="approval-empty" aria-labelledby="approval-empty-title">
          <Approvals size={20} aria-hidden="true" />
          <div>
            <h2 id="approval-empty-title">No immutable candidate</h2>
            <p>
              {localPilot ? "Analyze the verified homepage, then prepare its proposal in Ask Signal before an exact revision can request authority." : "Nothing waits on you. Signal adds decisions here as the weekly loop finds work."}
            </p>
            {localPilot ? <a href="/chat">Open Ask Signal</a> : null}
          </div>
        </section>
        {!localPilot ? <ReadinessSection definition={sectionDefinitions.approvals} /> : null}
      </>
    );
  }
  return (
    <>
      {notice === null ? null : <ProposalNotice notice={notice} />}
      <section className="approval-layout" aria-labelledby="approval-title">
        <aside className="approval-inbox" aria-label="Approval inbox">
          <div className="approval-inbox-heading">
            <h2>Approval inbox</h2>
            <span>1</span>
          </div>
          <div className="approval-inbox-row" aria-current="true">
            <FileCheck2 size={18} aria-hidden="true" />
            <span>
              <strong>Verified homepage metadata draft</strong>
              <small>Revision {current.revisionNumber} · Low risk</small>
            </span>
            <ArrowRight size={15} aria-hidden="true" />
          </div>
        </aside>
        <div className="approval-detail">
          <header className="approval-header">
            <div>
              <span className="command-label">Exact evidence-bound revision</span>
              <h2 id="approval-title">Add a meta description to the verified homepage</h2>
              <p>Review the evidence, exact field change, checks, authority, and recovery before deciding.</p>
            </div>
            <span className={`approval-state approval-state-${current.approvalStatus}`}>
              {approvalStatusLabel(current.approvalStatus)}
            </span>
          </header>
          <dl className="approval-proof-strip">
            <div><dt>Evidence</dt><dd><code>{current.evidenceId.slice(0, 8)}</code></dd></div>
            <div><dt>Checks</dt><dd>{current.tests.length} passed</dd></div>
            <div><dt>Authority</dt><dd>{current.approvalClass} supervised draft</dd></div>
            <div><dt>Maximum cost</dt><dd>{formatMinorUnits(current.maximumCostMinorUnits)}</dd></div>
          </dl>
          <section className="approval-section" aria-labelledby="change-title">
            <h3 id="change-title">What will change</h3>
            <dl className="change-scope">
              <div><dt>Resource</dt><dd><code>{current.resourceLocator}</code></dd></div>
              <div><dt>Field</dt><dd><code>meta_description</code></dd></div>
            </dl>
            <div className="change-diff" aria-label="Exact metadata difference">
              <div className="diff-before"><span>Before</span><code>Not present</code></div>
              <div className="diff-after"><span>After</span><code>{current.afterValue}</code></div>
            </div>
          </section>
          <div className="approval-columns">
            <section className="approval-section" aria-labelledby="basis-title">
              <h3 id="basis-title">Why this is proposed</h3>
              <p>
                {current.rationale ?? current.confidenceBasis}. The draft is grounded in the
                immutable owner-verified homepage observation shown above.
              </p>
              <ul className="check-list">
                {current.roleContributions.map((role) => (
                  <li key={role.role}><Verified size={14} aria-hidden="true" /> {roleLabel(role.role)}</li>
                ))}
              </ul>
            </section>
            <section className="approval-section" aria-labelledby="limits-title">
              <h3 id="limits-title">Limits and recovery</h3>
              <p>{current.recoverySummary}</p>
              <ul className="check-list">
                <li><Safety size={14} aria-hidden="true" /> No GitHub or provider write</li>
                <li><Safety size={14} aria-hidden="true" /> No customer page modified</li>
                <li><Safety size={14} aria-hidden="true" /> Expires {formatTimestamp(current.approvalExpiresAt)}</li>
              </ul>
            </section>
          </div>
          {current.model === null ? null : (
            <section className="approval-section" aria-labelledby="model-proof-title">
              <h3 id="model-proof-title">Model audit</h3>
              <dl className="change-scope">
                <div><dt>Requested</dt><dd><code>{current.model.modelRequested}</code></dd></div>
                <div><dt>Reported</dt><dd><code>{current.model.modelReported}</code></dd></div>
                <div><dt>Call</dt><dd><code>{current.model.callId}</code></dd></div>
                <div><dt>Provider response</dt><dd><code>{current.model.providerResponseId}</code></dd></div>
                <div><dt>Provider storage</dt><dd>{current.model.store ? "Enabled" : "Disabled"}</dd></div>
                <div><dt>Input tokens</dt><dd>{current.model.usage.inputTokens}</dd></div>
                <div><dt>Output tokens</dt><dd>{current.model.usage.outputTokens}</dd></div>
                <div><dt>Cached input</dt><dd>{current.model.usage.cachedInputTokens}</dd></div>
                <div><dt>Total tokens</dt><dd>{current.model.usage.totalTokens}</dd></div>
              </dl>
              <div className="exact-revision">
                <span>Prompt SHA-256</span><code>{current.model.promptSha256}</code>
                <span>Input SHA-256</span><code>{current.model.inputSha256}</code>
                <span>Output SHA-256</span><code>{current.model.outputSha256}</code>
              </div>
            </section>
          )}
          <div className="exact-revision">
            <span>Revision SHA-256</span><code>{current.revisionSha256}</code>
          </div>
          {current.approvalStatus === "pending" && decisionId !== undefined ? (
            <div className="approval-actions" aria-label="Approval decisions">
              <DecisionForm
                proposal={current}
                siteId={active.id}
                decisionId={decisionId}
                decision="approved"
                label="Approve exact draft"
                icon={Verified}
                primary
              />
              <DecisionForm
                proposal={current}
                siteId={active.id}
                decisionId={decisionId}
                decision="changes_requested"
                label="Request edits"
                icon={PencilLine}
              />
              <DecisionForm
                proposal={current}
                siteId={active.id}
                decisionId={decisionId}
                decision="rejected"
                label="Reject"
                icon={X}
                danger
              />
            </div>
          ) : (
            <div className="decision-result" role="status">
              {current.approvalStatus === "approved" ? (
                <Verified size={18} aria-hidden="true" />
              ) : (
                <Attention size={18} aria-hidden="true" />
              )}
              <span>
                <strong>{approvalStatusLabel(current.approvalStatus)}</strong>
                {current.approvalStatus === "expired"
                  ? " Prepare a new current revision to decide."
                  : " Decision recorded. No external operation was dispatched."}
              </span>
            </div>
          )}
        </div>
      </section>
    </>
  );
}

function CandidateInboxContent({
  revisions,
  siteId,
  decisionId,
  slack,
  selectedRevisionId,
  telegram,
  insights,
  selection,
}: {
  revisions: readonly DashboardCandidateRevision[];
  siteId: string;
  decisionId?: string;
  slack?: SlackState;
  selectedRevisionId?: string;
  telegram?: TelegramState;
  insights: OwnerInsights;
  selection: InboxSelection;
}) {
  const writer = insights.writer.state === "available" ? insights.writer.value : null;
  const facts = insights.facts.state === "available" ? insights.facts.value.filter((fact) => fact.status === "proposed") : [];
  const articles = (writer?.candidates ?? []).filter((candidate) => candidate.review_status === "pending" || (candidate.review_status === "approved" && !candidate.delivery_approval_id));
  const topicOf = (candidate: (typeof articles)[number]) => {
    const draft = writer?.drafts.find((item) => item.draft_id === candidate.draft_id);
    return writer?.briefs.find((item) => item.brief_id === draft?.brief_id)?.payload.topic ?? candidate.manifest.changed_files[0]?.path ?? "New article";
  };
  const ordered = [...revisions].sort((a, b) => Number(b.reviewStatus === "pending") - Number(a.reviewStatus === "pending"));
  const day = (iso: string) => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));
  type Entry = { kind: "fix" | "article" | "fact"; id: string; label: string; title: string; sub: string; age: string; waiting: boolean; href: string };
  const entries: Entry[] = [
    ...ordered.map((revision) => ({ kind: "fix" as const, id: revision.revisionId, label: revision.approvalClass === "A4" ? "Big change" : "Fix", title: revision.finding.title,
      sub: revision.reviewStatus === "pending" ? `${revision.approvalClass === "A4" ? "Several pages" : "One page"} · easy to undo` : reviewStatusLabel(revision.reviewStatus),
      age: day(revision.sealedAt), waiting: revision.reviewStatus === "pending", href: `/approvals?revision=${revision.revisionId}` })),
    ...articles.map((candidate) => ({ kind: "article" as const, id: candidate.candidate_id, label: "Article", title: `New article: ${topicOf(candidate)}`,
      sub: candidate.review_status === "pending" ? "Waiting for your review" : "Approved · pull request needs your approval", age: day(candidate.created_at), waiting: true, href: `/approvals?article=${candidate.candidate_id}` })),
    ...facts.map((fact) => ({ kind: "fact" as const, id: fact.fact_id, label: "Fact", title: `Confirm “${fact.statement}”`, sub: "Signal only writes facts you confirm", age: day(fact.created_at), waiting: true, href: `/approvals?fact=${fact.fact_id}` })),
  ];
  const waitingCount = entries.filter((entry) => entry.waiting).length;
  const filter = selection.kind ?? "all";
  const shown = entries.filter((entry) => filter === "all" || entry.kind === filter);
  const chosen =
    (selectedRevisionId && entries.find((entry) => entry.kind === "fix" && entry.id === selectedRevisionId)) ||
    (selection.article && entries.find((entry) => entry.kind === "article" && entry.id === selection.article)) ||
    (selection.fact && entries.find((entry) => entry.kind === "fact" && entry.id === selection.fact)) ||
    shown.find((entry) => entry.waiting) || shown[0] || null;
  if (selectedRevisionId !== undefined && !revisions.some((revision) => revision.revisionId === selectedRevisionId) && !selection.article && !selection.fact) {
    return <CandidateInboxUnavailable state="invalid" />;
  }
  const counts = { all: entries.length, fix: entries.filter((entry) => entry.kind === "fix").length, article: articles.length, fact: facts.length };
  const filters = [["all", "All"], ["fix", "Fixes"], ["article", "Articles"], ["fact", "Facts"]] as const;
  return (
    <>
      <header className="c-ph">
        <div>
          <span className="c-ml">Inbox · {waitingCount} waiting</span>
          <h1 className="c-h1"><span className="c-ln"><span>{waitingCount === 0 ? "Nothing waits on you." : `${waitingCount} ${waitingCount === 1 ? "decision waits" : "decisions wait"} on you.`}</span></span></h1>
        </div>
        <nav className="c-flt" aria-label="Filter">
          {filters.map(([key, label]) => (
            <a key={key} href={key === "all" ? "/approvals" : `/approvals?kind=${key}`} className={filter === key ? "on" : undefined} aria-current={filter === key ? "true" : undefined}>{label}<span>{counts[key]}</span></a>
          ))}
        </nav>
      </header>
      {entries.length === 0 ? (
        <section className="c-empty c-in" aria-labelledby="candidate-inbox-title">
          <span className="c-lat c-lat-d" aria-hidden="true" />
          <span className="c-ml c-ml-d">Inbox · empty</span>
          <h2 id="candidate-inbox-title">Nothing waits on you. Signal keeps working.</h2>
          <p className="c-empty-sub">Signal adds decisions here as the weekly loop finds work.</p>
          <a className="c-btn-accent" href="/changes">See what Signal did</a>
        </section>
      ) : (
        <section className="c-ib inbox-layout" aria-labelledby="candidate-inbox-title">
          <aside className="c-lst" aria-label="Candidate revision Inbox">
            {shown.length === 0 ? <div className="c-lst-empty">Nothing of this kind is waiting.</div> : shown.map((entry, index) => (
              <a key={`${entry.kind}:${entry.id}`} href={entry.href} className={`c-li c-rs${chosen?.kind === entry.kind && chosen.id === entry.id ? " on" : ""}`}
                aria-current={chosen?.kind === entry.kind && chosen.id === entry.id ? "true" : undefined} style={{ animationDelay: `${0.05 + index * 0.06}s` }}>
                <span className="c-li-top"><span className="c-pill o">{entry.label}</span><span className="c-mono c-age">{entry.age}</span></span>
                <b>{entry.title}</b>
                <span className="s">{entry.sub}</span>
              </a>
            ))}
          </aside>
          {chosen === null ? null : chosen.kind === "fix" ? (
            <FixDetail current={revisions.find((revision) => revision.revisionId === chosen.id)!} siteId={siteId} decisionId={decisionId} slack={slack} telegram={telegram} />
          ) : chosen.kind === "article" ? (
            <article className="inbox-detail c-det">
              <ArticleReview siteId={siteId} candidate={articles.find((candidate) => candidate.candidate_id === chosen.id)!}
                draft={writer?.drafts.find((draft) => draft.draft_id === articles.find((candidate) => candidate.candidate_id === chosen.id)?.draft_id) ?? null}
                topic={chosen.title.replace(/^New article: /, "")} />
            </article>
          ) : (
            <article className="inbox-detail c-det">
              <FactReview siteId={siteId} fact={facts.find((fact) => fact.fact_id === chosen.id)!} />
            </article>
          )}
        </section>
      )}
    </>
  );
}

function FixDetail({
  current,
  siteId,
  decisionId,
  slack,
  telegram,
}: {
  current: DashboardCandidateRevision;
  siteId: string;
  decisionId?: string;
  slack?: SlackState;
  telegram?: TelegramState;
}) {
  const pending = current.reviewStatus === "pending";
  const bigChange = current.approvalClass === "A4";
  const serp = serpPreview(current.before, current.after);
  return (
      <article className="inbox-detail">
        <div className="inbox-detail-body">
          <header className="decision-header">
            <div className="decision-tags">
              <span className="pill">{bigChange ? "Big change" : "Fix"}</span>
              <span className={`pill ${bigChange ? "pill-warning" : "pill-positive"}`}>
                {bigChange ? `${current.builtImpact?.pageCount ?? "Many"} pages` : "One page"}
              </span>
              <span className="pill">Undo by inverse change</span>
              <span className={`review-state review-${current.reviewStatus}`}>{reviewStatusLabel(current.reviewStatus)}</span>
            </div>
            <h2 id="candidate-inbox-title">{current.finding.title}</h2>
            <p className="decision-lede">{current.finding.summary}</p>
          </header>

          {bigChange && current.builtImpact ? (
            <div className="decision-callout warning" role="note">
              <Attention size={18} aria-hidden="true" />
              <span>
                <strong>{current.approvalClass} · {current.builtImpact.pageCount} built pages</strong> · Shared-template change ·
                Owner approval and MFA within 5 minutes required. No standing authorization.
              </span>
            </div>
          ) : null}

          {serp ? (
            <section className="decision-section" aria-labelledby="candidate-serp-title">
              <h3 id="candidate-serp-title" className="decision-label">What people will see in search</h3>
              <div className="c-serp-pair">
                <div className="c-serp">
                  <span className="c-serp-tag">Now</span>
                  <small>{serpCrumb(current.evidence.pageUrl)}</small>
                  <span className="t">{serp.before.title ? <span className={serp.titleChanged ? "c-del" : undefined}>{serp.before.title}</span> : <span className="c-soft">No title</span>}</span>
                  <span className="d">{serp.before.description ? <span className={serp.descriptionChanged ? "c-del" : undefined}>{serp.before.description}</span> : <em className="c-soft">No description. Search engines improvise one from the page.</em>}</span>
                </div>
                <div className="c-serp after">
                  <span className="c-serp-tag">After</span>
                  <small>{serpCrumb(current.evidence.pageUrl)}</small>
                  <span className="t">{serp.after.title ? <span className={serp.titleChanged ? "c-ins" : undefined}>{serp.after.title}</span> : serp.before.title}</span>
                  <span className="d">{serp.after.description ? <span className={serp.descriptionChanged ? "c-ins" : undefined}>{serp.after.description}</span> : serp.before.description}</span>
                </div>
              </div>
            </section>
          ) : null}

          <section className="decision-section" aria-labelledby="candidate-diff-title">
            <h3 id="candidate-diff-title" className="decision-label">What changes</h3>
            <div className="before-after" aria-label="Exact sealed before and after difference">
              <div className="before">
                <span className="ba-tag">Now{current.before === "" ? " (insert at destination)" : ""}</span>
                {current.before === "" ? <p className="ba-empty">Nothing is there yet.</p> : <code>{renderVisibleText(current.before)}</code>}
              </div>
              <div className="after">
                <span className="ba-tag">After{current.after === "" ? " (removed)" : ""}</span>
                <code>{renderVisibleText(current.after)}</code>
              </div>
            </div>
            <p className="decision-where">
              In <code>{current.sourcePath}</code> · seen on{" "}
              <a href={current.evidence.pageUrl} target="_blank" rel="noopener noreferrer">{current.evidence.pageUrl}</a>
            </p>
          </section>

          {current.builtImpact && <section className="decision-section" aria-labelledby="astro-built-impact-title">
            <h3 id="astro-built-impact-title" className="decision-label">Built HTML impact</h3>
            {current.builtImpact.samples.map(sample => <div key={sample.path}><p><code>{sample.path}</code></p><div className="before-after"><div className="before"><span className="ba-tag">Before</span><code>{renderVisibleText(sample.before)}</code></div><div className="after"><span className="ba-tag">After</span><code>{renderVisibleText(sample.after)}</code></div></div></div>)}
            <details><summary>Exact affected pages ({current.builtImpact.pageCount})</summary><ul>{current.builtImpact.pages.map(page => <li key={page}><code>{page}</code></li>)}</ul></details>
          </section>}

          <div className="decision-columns">
            <section className="decision-section" aria-labelledby="candidate-evidence-title">
              <h3 id="candidate-evidence-title" className="decision-label">What to expect</h3>
              <p>{current.expectedImpact}</p>
            </section>
            <section className="decision-section" aria-labelledby="candidate-checks-title">
              <h3 id="candidate-checks-title" className="decision-label">Checks passed</h3>
              <ul className="check-list">
                <li><Verified size={15} aria-hidden="true" /> Your site builds with this change, in an isolated copy</li>
                <li><Verified size={15} aria-hidden="true" /> {current.claimReviewRequired ? "Text claims require owner review" : "Deterministic recipe: no new claims"}</li>
                <li><Verified size={15} aria-hidden="true" /> Only <code>{current.sourcePath}</code> changes</li>
              </ul>
            </section>
          </div>

          <section className="decision-section" aria-labelledby="candidate-ships-title">
            <h3 id="candidate-ships-title" className="decision-label">How it ships</h3>
            <ol className="ship-steps">
              <li><span>1</span><strong>You decide on this exact change</strong><small>Approving records your decision. It does not write to your repository by itself.</small></li>
              <li><span>2</span><strong>Signal opens a pull request</strong><small>Only when your repository is connected and the change is still current.</small></li>
              <li><span>3</span><strong>You merge, Signal verifies</strong><small>Signal never merges or deploys. It checks the live page afterwards.</small></li>
            </ol>
            <p className="decision-recovery"><strong>If it needs undoing:</strong> {current.recoveryPlan}</p>
          </section>

          <details className="technical-details">
            <summary>Technical details</summary>
            <dl className="change-scope">
              <div><dt>Finding</dt><dd><code>{current.finding.id}</code> · <code>{current.finding.resourceLocator}</code></dd></div>
              <div><dt>Recipe release</dt><dd><code>{current.recipeReleaseId}</code></dd></div>
              <div><dt>Build</dt><dd>{current.build.command}</dd></div>
              <div><dt>Base commit</dt><dd><code>{current.baseSha}</code></dd></div>
              <div><dt>{current.evidence.kind === "indexnow_key" ? "Key digest" : "Manifest"}</dt><dd><code>{current.evidence.manifestSha256}</code></dd></div>
              <div><dt>Patch digest</dt><dd><code>{current.patchSha256}</code></dd></div>
              <div><dt>Build logs</dt><dd><code>{current.build.logsSha256}</code></dd></div>
              <div><dt>Revision SHA-256</dt><dd><code>{current.revisionSha256}</code></dd></div>
              <div><dt>Release content SHA-256</dt><dd><code>{current.releaseContentHash}</code></dd></div>
              {current.builtImpact ? <div><dt>Scope SHA-256</dt><dd><code>{current.builtImpact.scopeSha256}</code></dd></div> : null}
              {current.builtImpact ? <div><dt>Lockfile SHA-256</dt><dd><code>{current.builtImpact.lockfileSha256}</code></dd></div> : null}
            </dl>
          </details>
        </div>
        {pending && decisionId !== undefined ? (
          <div className="decision-bar" aria-label="Candidate revision decisions">
            <span className="decision-hint">Approving records your decision only. Nothing goes live until you merge.</span>
            {(!current.builtImpact && slack?.availability === "bound" && slack.link_id !== null) ||
            (telegram?.availability === "bound" && telegram.link_id !== null) ? (
              <details className="decision-more">
                <summary>Ask in Slack or Telegram</summary>
                <div className="decision-more-panel">
                  {!current.builtImpact && slack?.availability === "bound" && slack.link_id !== null ? <SlackRequest siteId={siteId}
                    bindingId={slack.binding_id} channelId={slack.channel_id} revisionId={current.revisionId}
                    revisionSha256={current.revisionSha256} /> : null}
                  {telegram?.availability === "bound" && telegram.link_id !== null ? <TelegramRequest siteId={siteId}
                    bindingId={telegram.binding_id} revisionId={current.revisionId}
                    revisionSha256={current.revisionSha256} /> : null}
                </div>
              </details>
            ) : null}
            <div className="decision-actions">
              <CandidateDecisionForm revision={current} siteId={siteId} decisionId={decisionId} decision="rejected" label="Reject" icon={X} danger />
              <CandidateDecisionForm revision={current} siteId={siteId} decisionId={decisionId} decision="changes_requested" label="Request changes" icon={PencilLine} />
              <CandidateDecisionForm revision={current} siteId={siteId} decisionId={decisionId} decision="approved" label="Approve exact revision" icon={Verified} primary />
            </div>
          </div>
        ) : (
          <div className="decision-result" role="status">
            <Attention size={18} aria-hidden="true" />
            <span>
              <strong>{reviewStatusLabel(current.reviewStatus)}</strong>{" "}
              {current.reviewStatus === "superseded" || current.reviewStatus === "stale_base"
                ? "A new exact revision is required before a decision."
                : "Decision recorded. No external operation was dispatched."}
            </span>
          </div>
        )}
      </article>
  );
}

function CandidateInboxUnavailable({ state }: { state: Exclude<DashboardCandidateInbox["state"], "available"> }) {
  const message = state === "unavailable" ? "The sealed candidate Inbox is not connected in this environment." : state === "not_authenticated" ? "Sign in to review sealed candidate revisions." : "The candidate Inbox response could not be safely used.";
  return <section className="approval-empty" aria-labelledby="candidate-inbox-unavailable"><Unavailable size={20} aria-hidden="true" /><div><h2 id="candidate-inbox-unavailable">Inbox unavailable</h2><p>{message}</p></div></section>;
}

function operationStatus(operation: DashboardGithubPrOperation): string {
  if (operation.state === "dispatching" || operation.state === "outcome_unknown") return "Outcome unknown; reconciliation required";
  if (operation.state === "opened") return "Pull request opened";
  if (operation.state === "blocked") return "Blocked";
  return `Preparing: ${readableCode(operation.step).toLowerCase()}`;
}

function DeliveryEvidence({ observation, available, recoveryPlan }: { observation: DashboardDeliveryObservation | undefined; available: boolean; recoveryPlan: string }) {
  const recorded = observation?.state === "completed";
  const stages = [
    ["PR opened", true],
    ["Checks", recorded && (observation.checksCount ?? 0) > 0],
    ["Merged", recorded && observation.mergedSha !== null],
    ["Deployed", recorded && observation.stage === "deployed" && observation.deploymentId !== null],
    ["Live verified", recorded && observation.outcome === "verified"],
  ] as const;
  const labels = { verified: "Exact page verified", not_yet_deployed: "Waiting for customer delivery", inconclusive: "Inconclusive; owner review required", regressed: "Regression observed; owner review required" };
  return <>
    <ol className="work-pipeline delivery-pipeline" aria-label="Observed delivery stages">
      {stages.map(([label, complete], index) => <li className={complete ? "complete" : "pending"} key={label}>
        <span className="pipeline-marker">{complete ? <Verified size={15} aria-hidden="true" /> : index + 1}</span>
        <strong>{label}</strong><small>{complete ? "Evidence recorded" : "Not observed"}</small>
      </li>)}
    </ol>
    <p role={observation?.outcome === "regressed" ? "alert" : "status"}>{recorded && observation.outcome ? labels[observation.outcome] : observation ? "Observation outcome unknown; no live success recorded" : available ? "No delivery observation recorded" : "Delivery evidence unavailable"}</p>
    {recorded ? <>
      <dl className="change-facts">
        <div><dt>Last checked</dt><dd><time dateTime={observation.observedAt}>{formatTimestamp(observation.observedAt)}</time></dd></div>
        {observation.checksCount !== null ? <div><dt>Checks observed</dt><dd>{observation.passedCount} passed of {observation.checksCount}</dd></div> : null}
        {observation.deploymentId ? <div><dt>Deployment</dt><dd>{observation.deploymentId}</dd></div> : null}
      </dl>
      <details className="technical-details">
        <summary>Delivery evidence</summary>
        <dl className="change-scope">
          {observation.reason ? <div><dt>Reason</dt><dd>{readableCode(observation.reason)} <code>{observation.reason}</code></dd></div> : null}
          {observation.mergedSha ? <div><dt>Merged commit</dt><dd><code>{observation.mergedSha}</code></dd></div> : null}
          {observation.fetchedSha256 ? <div><dt>Fetched page digest</dt><dd><code>{observation.fetchedSha256}</code></dd></div> : null}
          <div><dt>Receipt digest</dt><dd><code>{observation.receiptSha256}</code></dd></div>
        </dl>
      </details>
    </> : null}
    {observation?.outcome === "not_yet_deployed" ? <p>Next observation no earlier than <time dateTime={observation.nextObserveAt}>{formatTimestamp(observation.nextObserveAt)}</time>.</p> : null}
    {observation?.postconditions.map(condition => <p key={condition.field}><strong>{condition.field}</strong>: {condition.matched ? "Matched" : "Mismatched"}<br />Expected: <code>{renderVisibleText(JSON.stringify(condition.expected))}</code><br />Observed: <code>{renderVisibleText(JSON.stringify(condition.observed))}</code></p>)}
    {observation?.outcome === "inconclusive" || observation?.outcome === "regressed" ? <p><strong>Recovery plan:</strong> {renderVisibleText(observation.recoveryPlan ?? recoveryPlan)} Preserve later edits; conflicts require owner review. No revert has been dispatched.</p> : null}
    {observation?.canonicalReceipt ? <details className="delivery-receipt"><summary>Exact observation evidence</summary><pre>{renderVisibleText(observation.canonicalReceipt)}</pre></details> : null}
    <p className="change-footnote">Signal does not merge or deploy. These are external/manual delivery observations, not a signed delivery certification.</p>
  </>;
}

function CandidateDecisionForm({ revision, siteId, decisionId, decision, label, icon: Icon, primary = false, danger = false }: { revision: DashboardCandidateRevision; siteId: string; decisionId: string; decision: "approved" | "rejected" | "changes_requested"; label: string; icon: LucideIcon; primary?: boolean; danger?: boolean }) {
  return <form action="/actions/decide-candidate-revision" method="post"><input type="hidden" name="site_id" value={siteId} /><input type="hidden" name="revision_id" value={revision.revisionId} /><input type="hidden" name="revision_sha256" value={revision.revisionSha256} /><input type="hidden" name="decision_id" value={decisionId} /><input type="hidden" name="decision" value={decision} /><button className={primary ? "primary-command" : danger ? "danger-command" : "secondary-command"} type="submit"><Icon size={16} aria-hidden="true" /> {label}</button></form>;
}

function reviewStatusLabel(status: CandidateReviewStatus): string {
  return { pending: "Pending review", approved: "Approved", rejected: "Rejected", changes_requested: "Changes requested", superseded: "Superseded", stale_base: "Base changed" }[status];
}

function renderVisibleText(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f-\u009f\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g, (character) => `\\u${character.codePointAt(0)?.toString(16).padStart(4, "0")}`);
}

function DecisionForm({
  proposal,
  siteId,
  decisionId,
  decision,
  label,
  icon: Icon,
  primary = false,
  danger = false,
}: {
  proposal: DashboardProposal;
  siteId: string;
  decisionId: string;
  decision: "approved" | "rejected" | "changes_requested";
  label: string;
  icon: LucideIcon;
  primary?: boolean;
  danger?: boolean;
}) {
  return (
    <form action="/actions/decide-proposal" method="post">
      <input type="hidden" name="site_id" value={siteId} />
      <input type="hidden" name="approval_request_id" value={proposal.approvalRequestId} />
      <input type="hidden" name="revision_sha256" value={proposal.revisionSha256} />
      <input type="hidden" name="decision_id" value={decisionId} />
      <input type="hidden" name="decision" value={decision} />
      <button
        className={primary ? "primary-command" : danger ? "danger-command" : "secondary-command"}
        type="submit"
      >
        <Icon size={16} aria-hidden="true" /> {label}
      </button>
    </form>
  );
}

function approvalStatusLabel(status: DashboardProposal["approvalStatus"]): string {
  return {
    pending: "Awaiting approval",
    expired: "Expired",
    approved: "Approved",
    rejected: "Rejected",
    changes_requested: "Edits requested",
  }[status];
}

function roleLabel(role: DashboardProposal["roleContributions"][number]["role"]): string {
  return {
    technical_seo: "Technical evidence supported",
    content_strategy: "Metadata draft prepared",
    independent_reviewer: "Independent scope review passed",
    coordinator: "Exact approval requested",
  }[role];
}

function formatMinorUnits(value: number): string {
  return `$${(value / 100).toFixed(2)}`;
}

function SettingsContent({
  session,
  sites,
}: {
  session: DashboardSession;
  sites: DashboardSiteDirectory;
}) {
  const active = selectedSite(session, sites);
  return (
    <>
      <section
        className="settings-section"
        aria-labelledby="business-profile-title"
      >
        <SectionHeading
          id="business-profile-title"
          title="Site details"
          detail="Set when the site was added. The facts Signal may use in content live in Business facts."
        />
        <dl className="settings-facts">
          <ReadOnlyField
            label="Site name"
            value={active?.name ?? "No active site"}
          />
          <ReadOnlyField
            label="Primary origin"
            value={active?.primaryOrigin ?? "Not selected"}
          />
          <ReadOnlyField
            label="Time zone"
            value={active?.timezone ?? "Unavailable"}
          />
          <ReadOnlyField
            label="Reporting currency"
            value={active?.reportingCurrency ?? "Unavailable"}
          />
        </dl>
        {active !== null ? <a className="text-link" href="/business-brain">Open Business facts</a> : null}
      </section>
      {session.state === "authenticated" && session.role === "owner" && active ? (
        <BrandDocuments siteId={active.id} />
      ) : null}
    </>
  );
}

function UnavailableSection({ definition }: { definition: SectionDefinition }) {
  const chart =
    definition === sectionDefinitions.analytics
      ? {
          title: "Performance over time",
          description:
            "Search performance appears after an exact property import is validated.",
          actionHref: "/connectors",
          actionLabel: "Review connectors",
        }
      : definition === sectionDefinitions.usage
        ? {
            title: "Cost by category",
            description:
              "Settled and reserved cost appears after attributed usage is recorded.",
            actionHref: "/settings",
            actionLabel: "Review limits",
          }
        : null;
  return (
    <>
      {chart === null ? null : <NoDataChart {...chart} />}
      <ReadinessSection definition={definition} />
    </>
  );
}

function VisibilityContent({
  session,
  sites,
}: {
  session: DashboardSession;
  sites: DashboardSiteDirectory;
}) {
  const selected =
    session.state === "authenticated" &&
    sites.state === "available" &&
    sites.sites.some((site) => site.id === session.activeSiteId);
  return (
    <>
      <section className="data-chart" aria-labelledby="ai-visibility-baseline-title" data-no-synthetic-data="true">
        <header className="data-chart-header">
          <div>
            <h2 id="ai-visibility-baseline-title">Citation baseline</h2>
            <p>Coverage is shown by provider and question version. Missing, unavailable, or malformed observations are incomplete, not zero.</p>
          </div>
          <span className="data-state">No source</span>
        </header>
        <div className="chart-plot">
          <div className="chart-empty-state">
            <strong>{selected ? "No observations yet" : "Select a site first"}</strong>
            <span>Provider API answers can differ from consumer applications. No visibility score is fabricated.</span>
          </div>
        </div>
      </section>
      <ReadinessSection definition={sectionDefinitions.visibility} />
    </>
  );
}

function ReadinessSection({
  definition,
}: {
  definition: SectionDefinition;
}) {
  return (
    <section className="readiness" aria-labelledby="readiness-title">
      <h2 id="readiness-title">What has to be true first</h2>
      <ul className="readiness-list">
        {definition.rows.map(({ label, source, state }) => (
          <li key={label}>
            <span className="readiness-item">
              <strong>{label}</strong>
              <small>{source}</small>
            </span>
            <span className="readiness-state">{state}</span>
          </li>
        ))}
      </ul>
      <p className="readiness-note">
        {definition.ledgerDescription} Nothing here is simulated: the page stays
        empty until each requirement above is met.
      </p>
    </section>
  );
}

function NoDataChart({
  title,
  description,
  actionHref,
  actionLabel,
}: {
  title: string;
  description: string;
  actionHref: string;
  actionLabel: string;
}) {
  return (
    <section
      className="data-chart"
      aria-labelledby={`${title.toLowerCase().replaceAll(" ", "-")}-title`}
      data-no-synthetic-data="true"
    >
      <header className="data-chart-header">
        <div>
          <h2 id={`${title.toLowerCase().replaceAll(" ", "-")}-title`}>
            {title}
          </h2>
          <p>{description}</p>
        </div>
        <span className="data-state">No source</span>
      </header>
      <div className="chart-plot">
        <div className="chart-empty-state">
          <strong>No observations yet</strong>
          <span>No customer metrics are shown until source evidence exists.</span>
          <a href={actionHref}>{actionLabel}</a>
        </div>
      </div>
    </section>
  );
}

function SectionHeading({
  id,
  title,
  detail,
  meta,
}: {
  id: string;
  title: string;
  detail: string;
  meta?: string;
}) {
  return (
    <div className="section-heading">
      <div>
        <h2 id={id}>{title}</h2>
        <p>{detail}</p>
      </div>
      {meta === undefined ? null : <span className="state-chip">{meta}</span>}
    </div>
  );
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function SiteDirectoryPanel({
  session,
  directory,
  onboardingRequestId,
}: {
  session: DashboardSession;
  directory: DashboardSiteDirectory;
  onboardingRequestId?: string;
}) {
  if (session.state !== "authenticated") return null;
  if (directory.state !== "available") {
    return (
      <section
        className="ledger-section site-panel"
        aria-labelledby="site-directory-title"
      >
        <SectionHeading
          id="site-directory-title"
          title="Authorized sites"
          detail="Current site scope derived from the tenant session."
          meta="Unavailable"
        />
        <div className="inline-empty">
          <Attention size={16} aria-hidden="true" />
          {directory.state === "invalid"
            ? "The site directory response was rejected."
            : "The site directory could not be reached."}
        </div>
      </section>
    );
  }
  const activeSite = directory.sites.find(
    (site) => session.activeSiteId === site.id,
  );

  return (
    <section
      className="ledger-section site-panel"
      aria-labelledby="site-directory-title"
    >
      <SectionHeading
        id="site-directory-title"
        title="Authorized sites"
        detail={`Sites in ${directory.tenantName}. Signal works on the current one.`}
        meta={`${directory.sites.length} ${directory.sites.length === 1 ? "site" : "sites"}`}
      />
      {directory.sites.length === 0 ? (
        <div className="inline-empty">
          <Unavailable size={16} aria-hidden="true" />
          You don’t have access to any sites here yet. Add one below.
        </div>
      ) : (
        <ul className="site-list">
          {directory.sites.map((site) => {
            const active = session.activeSiteId === site.id;
            return (
              <li key={site.id} className={active ? "active" : undefined}>
                <Origin className="row-mark" size={16} aria-hidden="true" />
                <span className="site-identity">
                  <strong>{site.name}</strong>
                  <code>{site.primaryOrigin}</code>
                </span>
                <span className={`site-state ${site.state}`}>
                  {titleCase(site.state)}
                </span>
                <span className={`site-ownership ${site.ownershipStatus}`}>
                  {site.ownershipStatus === "verified" ? (
                    <Verified size={14} aria-hidden="true" />
                  ) : site.ownershipStatus === "reverification_required" ? (
                    <Attention size={14} aria-hidden="true" />
                  ) : null}
                  {site.ownershipStatus === "verified"
                    ? "Ownership verified"
                    : site.ownershipStatus === "reverification_required"
                      ? "Reverification required"
                      : "Ownership unverified"}
                </span>
                {active ? (
                  <span className="site-selection active">
                    <Verified size={16} aria-hidden="true" /> Current
                  </span>
                ) : (
                  <form action="/auth/select-site" method="post">
                    <input type="hidden" name="site_id" value={site.id} />
                    <input
                      type="hidden"
                      name="session_version"
                      value={session.sessionVersion}
                    />
                    <button className="site-selection" type="submit">
                      Select
                    </button>
                  </form>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {session.role === "owner" &&
      activeSite !== undefined &&
      activeSite.ownershipStatus !== "verified" ? (
        <OriginVerification
          siteId={activeSite.id}
          siteName={activeSite.name}
          origin={activeSite.primaryOrigin}
          ownershipStatus={activeSite.ownershipStatus}
        />
      ) : null}
      {session.role === "owner" && onboardingRequestId !== undefined ? (
        <details className="site-onboarding">
          <summary>
            <Plus size={16} aria-hidden="true" /> Add site
          </summary>
          <form action="/auth/create-site" method="post">
            <input
              type="hidden"
              name="idempotency_key"
              value={onboardingRequestId}
            />
            <input
              type="hidden"
              name="expected_session_version"
              value={session.sessionVersion}
            />
            <label>
              <span>Name</span>
              <input
                name="name"
                required
                maxLength={200}
                autoComplete="organization"
              />
            </label>
            <label className="origin-field">
              <span>HTTPS origin</span>
              <input
                name="primary_origin"
                type="url"
                required
                maxLength={2048}
                placeholder="https://www.example.com"
                autoComplete="url"
              />
            </label>
            <label>
              <span>Time zone</span>
              <input
                name="timezone"
                required
                maxLength={128}
                defaultValue="UTC"
                list="signal-timezones"
              />
              <datalist id="signal-timezones">
                <option value="UTC" />
                <option value="America/Phoenix" />
                <option value="America/New_York" />
                <option value="Europe/London" />
                <option value="Asia/Kolkata" />
              </datalist>
            </label>
            <label>
              <span>Currency</span>
              <select name="reporting_currency" defaultValue="USD">
                <option value="USD">USD</option>
                <option value="EUR">EUR</option>
                <option value="GBP">GBP</option>
                <option value="INR">INR</option>
                <option value="CAD">CAD</option>
                <option value="AUD">AUD</option>
              </select>
            </label>
            <button type="submit">
              <Plus size={16} aria-hidden="true" /> Add
            </button>
          </form>
        </details>
      ) : null}
    </section>
  );
}

function OrganizationPanel({
  session,
  directory,
}: {
  session: DashboardSession;
  directory: DashboardOrganizationDirectory;
}) {
  if (session.state === "authenticated" || directory.state === "absent")
    return null;
  // Expired, invalid and unavailable directories are already explained by the
  // welcome panel's sign-in notice; a second card would repeat it.
  if (directory.state !== "available") return null;
  return (
    <section
      className="ledger-section organization-panel"
      aria-labelledby="organization-title"
    >
      <SectionHeading
        id="organization-title"
        title="Choose organization"
        detail="Continue with one current organization membership."
        meta={`${directory.organizations.length} ${
          directory.organizations.length === 1
            ? "organization"
            : "organizations"
        }`}
      />
      {directory.organizations.length === 0 ? (
        <div className="inline-empty">
          <Unavailable size={16} aria-hidden="true" />
          This identity has no active organization membership.
        </div>
      ) : (
        <ul className="organization-list">
          {directory.organizations.map((organization) => (
            <li key={organization.tenantId}>
              <Organization className="row-mark" size={16} aria-hidden="true" />
              <span className="organization-identity">
                <strong>{organization.name}</strong>
                <small>{titleCase(organization.role)} membership</small>
              </span>
              <form action="/auth/select-organization" method="post">
                <input
                  type="hidden"
                  name="tenant_id"
                  value={organization.tenantId}
                />
                <button type="submit">Continue</button>
              </form>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function WorkspaceContext({
  session,
  sites,
}: {
  session: DashboardSession;
  sites: DashboardSiteDirectory;
}) {
  const active = selectedSite(session, sites);
  const host = (origin: string) => { try { return new URL(origin).host; } catch { return origin; } };
  if (session.state !== "authenticated" || sites.state !== "available") {
    return (
      <div className="workspace-switcher c-site" aria-label="Current authorized workspace">
        <i className="off" aria-hidden="true" />
        <span>{workspaceLabel(session, sites)}</span>
        <span className="c-site-detail">{workspaceDetail(session, sites)}</span>
      </div>
    );
  }
  return (
    <details className="workspace-switcher c-site-menu" aria-label="Current authorized workspace">
      <summary className="c-site">
        <i className={active?.ownershipStatus === "verified" ? "" : "off"} aria-hidden="true" />
        <span>{active ? host(active.primaryOrigin) : workspaceLabel(session, sites)}</span>
      </summary>
      <div className="c-menu" role="menu">
        <span className="c-ml c-menu-label">{sites.tenantName}</span>
        {sites.sites.map((site) => site.id === active?.id ? (
          <span key={site.id} className="c-menu-i current" role="menuitem" aria-current="true">
            {host(site.primaryOrigin)}<span className="c-ml">{site.ownershipStatus === "verified" ? "Current" : "Verify ownership"}</span>
          </span>
        ) : (
          <form key={site.id} action="/auth/select-site" method="post">
            <input type="hidden" name="site_id" value={site.id} />
            <input type="hidden" name="session_version" value={session.sessionVersion} />
            <button className="c-menu-i" type="submit" role="menuitem">{host(site.primaryOrigin)}<span className="c-ml">{site.ownershipStatus === "verified" ? "Switch" : "Setup"}</span></button>
          </form>
        ))}
        {session.role === "owner" ? <a className="c-menu-i" role="menuitem" href="/settings#site-directory-title">Add a site</a> : null}
      </div>
    </details>
  );
}

function AccountState({
  session,
  organizations,
  compact = false,
}: {
  session: DashboardSession;
  organizations: DashboardOrganizationDirectory;
  compact?: boolean;
}) {
  const label = sessionLabel(session);
  if (session.state === "authenticated") {
    return (
      <form className="account-form" action="/auth/logout" method="post">
        <button
          className={`account-state authenticated interactive${compact ? " compact" : ""}`}
          type="submit"
          aria-label={`Log out ${label} session`}
          title="Log out"
        >
          <SignOut size={16} aria-hidden="true" />
          <span>{label}</span>
        </button>
      </form>
    );
  }
  if (organizations.state === "available") {
    const hasOrganizations = organizations.organizations.length > 0;
    return (
      <div
        className={`account-state identity${compact ? " compact" : ""}`}
        aria-label={
          hasOrganizations
            ? "Account session: choose organization"
            : "Account session: no active organizations"
        }
      >
        {hasOrganizations ? (
          <Organization size={16} aria-hidden="true" />
        ) : (
          <Unavailable size={16} aria-hidden="true" />
        )}
        <span>
          {hasOrganizations ? "Choose organization" : "No organizations"}
        </span>
      </div>
    );
  }
  if (session.state === "signed_out" && organizations.state === "absent") {
    return (
      <form className="account-form" action="/auth/login" method="post">
        <button
          className={`account-state interactive${compact ? " compact" : ""}`}
          type="submit"
          aria-label="Sign in to Signal"
        >
          <SignIn size={16} aria-hidden="true" />
          <span>Sign in</span>
        </button>
      </form>
    );
  }
  const recoveryState =
    organizations.state === "unavailable" ? "unavailable" : "invalid";
  return (
    <form
      className="account-form"
      action="/auth/clear-browser-state"
      method="post"
    >
      <button
        className={`account-state ${recoveryState} interactive${compact ? " compact" : ""}`}
        type="submit"
        aria-label={`Clear browser state after ${label.toLowerCase()}`}
        title="Clear browser state"
      >
        <Clear size={16} aria-hidden="true" />
        <span>Clear browser state</span>
      </button>
    </form>
  );
}

function AuthNotice({ notice }: { notice: AuthNoticeKey }) {
  const content = authNoticeContent(notice);
  return (
    <div className={`identity-alert ${content.tone}`} role="status">
      {content.tone === "positive" ? (
        <Verified size={16} aria-hidden="true" />
      ) : (
        <Attention size={16} aria-hidden="true" />
      )}
      <div>
        <strong>{content.title}</strong>
        <span>{content.detail}</span>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <a className="brand" href="/" aria-label="Signal home">
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></span>
      signal
    </a>
  );
}

const TABS: { section: DashboardSection; label: string; href: string; icon: LucideIcon }[] = [
  { section: "overview", label: "Home", href: "/", icon: Overview },
  { section: "approvals", label: "Inbox", href: "/approvals", icon: Approvals },
  { section: "changes", label: "Activity", href: "/changes", icon: Changes },
  { section: "analytics", label: "Results", href: "/analytics", icon: Analytics },
];

/* Phone-only shortcut to the four places an owner checks most; the full menu stays in the top bar. */
function TabBar({ activeSection, pendingDecisions }: { activeSection: DashboardSection; pendingDecisions: number }) {
  return (
    <nav className="tab-bar" aria-label="Shortcuts">
      {TABS.map(({ section, label, href, icon: Icon }) => (
        <a key={section} href={href} aria-current={section === activeSection ? "page" : undefined}>
          <Icon size={20} aria-hidden="true" />
          <span>{label}</span>
          {section === "approvals" && pendingDecisions > 0 ? (
            <span className="tab-count" aria-label={`${pendingDecisions} waiting`}>{pendingDecisions}</span>
          ) : null}
        </a>
      ))}
    </nav>
  );
}

function Navigation({
  groups,
  activeSection,
  pendingDecisions,
}: {
  groups: NavigationGroup[];
  activeSection: DashboardSection;
  pendingDecisions: number;
}) {
  return (
    <nav className="navigation" aria-label="Sections">
      {groups.map((group) => (
        <div className="nav-group" key={group.label}>
          <span className="nav-label">{group.label}</span>
          {group.items.map(({ label, href, section }) => (
            <a
              key={section}
              href={href}
              className={section === (NAV_FAMILY[activeSection] ?? activeSection) ? "active" : undefined}
              aria-current={section === (NAV_FAMILY[activeSection] ?? activeSection) ? "page" : undefined}
            >
              <span className="nav-mark" aria-hidden="true" />
              <span>{label}</span>
              {section === "approvals" && pendingDecisions > 0 ? (
                <span className="nav-count" aria-label={`${pendingDecisions} waiting`}>{pendingDecisions}</span>
              ) : null}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}

const STATE_MARKS: Record<StatusTone, LucideIcon | null> = {
  positive: Verified,
  warning: Attention,
  neutral: null,
};

type StatusTone = "positive" | "warning" | "neutral";

function StatusItem({
  label,
  value,
  tone,
  className = "status-item",
}: {
  label: string;
  value: string;
  tone: StatusTone;
  className?: string;
}) {
  const Mark = STATE_MARKS[tone];
  return (
    <div className={className}>
      <small>{label}</small>
      <strong>
        {Mark === null ? null : (
          <Mark className={`state-mark ${tone}`} size={14} aria-hidden="true" />
        )}
        {value}
      </strong>
    </div>
  );
}

function CapabilityGroup({
  title,
  capabilities,
}: {
  title: string;
  capabilities: SignalCapability[];
}) {
  return (
    <div className="capability-group">
      <div className="capability-group-heading">
        <h3>{title}</h3>
        <span>{capabilities.length}</span>
      </div>
      {capabilities.length > 0 ? (
        <ul>
          {capabilities.map((capability) => (
            <li key={capability.key}>
              {capability.availability === "internal_only" ? (
                <Meter size={16} aria-hidden="true" />
              ) : (
                <Authority size={16} aria-hidden="true" />
              )}
              <code>{capability.key}</code>
              <span>
                {capability.availability === "internal_only"
                  ? "Internal"
                  : "Disabled"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="group-empty">None reported.</p>
      )}
    </div>
  );
}

function dependencyLabel(value: DashboardSnapshot["dependencies"]): string {
  if (value === "ready") return "Ready";
  if (value === "not_ready") return "Needs setup";
  return "Unknown";
}

function sessionLabel(session: DashboardSession): string {
  if (session.state === "authenticated") {
    return `${titleCase(session.role)}${session.authenticationLevel === "mfa" ? " / MFA" : ""}`;
  }
  if (session.state === "invalid") return "Session rejected";
  if (session.state === "unavailable") return "Session unavailable";
  return "Signed out";
}

function sessionAttention(
  session: DashboardSession,
  organizations: DashboardOrganizationDirectory,
): { title: string; detail: string } {
  if (organizations.state === "available") {
    if (organizations.organizations.length === 0) {
      return {
        title: "No active organization membership",
        detail: "This verified identity cannot establish tenant authority.",
      };
    }
    return {
      title: "Organization selection required",
      detail:
        "Choose the organization you want to work in.",
    };
  }
  if (organizations.state === "expired") {
    return {
      title: "Identity session expired",
      detail: "Your sign-in expired. Clear it and sign in again.",
    };
  }
  if (organizations.state === "invalid") {
    return {
      title: "Sign-in not confirmed",
      detail:
        "The sign-in response did not check out, so nothing was unlocked. Sign in again.",
    };
  }
  if (organizations.state === "unavailable") {
    return {
      title: "Identity service unavailable",
      detail:
        "Signal could not confirm your organizations, so nothing is unlocked until it can.",
    };
  }
  if (session.state === "invalid") {
    return {
      title: "Browser session rejected",
      detail: "Your browser session did not check out, so nothing was unlocked. Sign in again.",
    };
  }
  if (session.state === "unavailable") {
    return {
      title: "Session service unavailable",
      detail: "Signal could not confirm your session, so nothing is unlocked until it can.",
    };
  }
  return {
    title: "Sign in required",
    detail: "Sign in to see your sites.",
  };
}

function workspaceLabel(
  session: DashboardSession,
  sites: DashboardSiteDirectory,
): string {
  if (session.state !== "authenticated") return "Signal workspace";
  return sites.state === "available"
    ? sites.tenantName
    : `Workspace ${session.tenantId.slice(0, 8)}`;
}

function workspaceDetail(
  session: DashboardSession,
  sites: DashboardSiteDirectory,
): string {
  if (session.state !== "authenticated") return "No site selected";
  if (sites.state === "available") {
    const active = selectedSite(session, sites);
    if (active !== null) return active.primaryOrigin;
    return `${sites.sites.length} authorized ${sites.sites.length === 1 ? "site" : "sites"}`;
  }
  return sites.state === "invalid"
    ? "Site access rejected"
    : "Site access unavailable";
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function authNoticeContent(notice: AuthNoticeKey): {
  title: string;
  detail: string;
  tone: "positive" | "warning";
} {
  if (notice === "browser-cleared") {
    return {
      title: "Browser state cleared",
      detail:
        "Signal’s sign-in cookies were removed from this browser. Signal did not end the server session.",
      tone: "positive",
    };
  }
  if (notice === "identity-ready") {
    return {
      title: "Identity verified",
      detail: "Choose an organization to continue.",
      tone: "positive",
    };
  }
  if (notice === "signed-in") {
    return {
      title: "Organization selected",
      detail:
        "Your access was rechecked. Choose a site to work on.",
      tone: "positive",
    };
  }
  if (notice === "site-selected") {
    return {
      title: "Site selected",
      detail:
        "Your access was rechecked and Signal is now working on this site.",
      tone: "positive",
    };
  }
  if (notice === "site-created") {
    return {
      title: "Site added",
      detail: "The new site is selected. Verify you own it before Signal does any work.",
      tone: "positive",
    };
  }
  if (notice === "logged-out") {
    return {
      title: "Signed out",
      detail:
        "Your session was ended and this browser was signed out.",
      tone: "positive",
    };
  }
  if (notice === "callback-rejected") {
    return {
      title: "Sign-in response rejected",
      detail:
        "The sign-in response could not be confirmed, so nothing was unlocked.",
      tone: "warning",
    };
  }
  if (notice === "organization-rejected") {
    return {
      title: "Organization selection rejected",
      detail:
        "That organization could not be confirmed for you right now.",
      tone: "warning",
    };
  }
  if (notice === "site-conflict") {
    return {
      title: "Site context changed",
      detail:
        "Another request updated this session. Review the current site before trying again.",
      tone: "warning",
    };
  }
  if (notice === "site-create-conflict") {
    return {
      title: "Site setup changed",
      detail:
        "Another request updated this session. Review the site list before trying again.",
      tone: "warning",
    };
  }
  if (notice === "site-create-rejected") {
    return {
      title: "Site setup rejected",
      detail:
        "Signal could not confirm you are an owner, or the site details were not valid.",
      tone: "warning",
    };
  }
  if (notice === "site-create-failed") {
    return {
      title: "Site setup unavailable",
      detail: "The site was not added. Try again in a moment.",
      tone: "warning",
    };
  }
  if (notice === "site-rejected") {
    return {
      title: "Site selection rejected",
      detail:
        "Signal could not confirm your access to that site.",
      tone: "warning",
    };
  }
  if (notice === "site-failed") {
    return {
      title: "Site service unavailable",
      detail:
        "The current site was not changed.",
      tone: "warning",
    };
  }
  if (notice === "logout-rejected") {
    return {
      title: "Logout could not be authorized",
      detail:
        "Signal could not confirm the sign-out. Clear this browser only if you need to.",
      tone: "warning",
    };
  }
  if (notice === "request-rejected") {
    return {
      title: "Browser request rejected",
      detail: "The request did not come from this dashboard, so it was refused.",
      tone: "warning",
    };
  }
  return {
    title:
      notice === "organization-failed"
        ? "Organization service unavailable"
        : notice === "logout-failed"
          ? "Logout service unavailable"
          : "Sign-in service unavailable",
    detail:
      notice === "logout-failed"
        ? "Signal could not confirm the sign-out, so you are still signed in here."
        : "Nothing was unlocked. Try signing in again.",
    tone: "warning",
  };
}

function systemPosture(snapshot: DashboardSnapshot): {
  label: string;
  detail: string;
  tone: "positive" | "warning" | "neutral";
  icon: LucideIcon;
} {
  if (snapshot.connection === "misconfigured") {
    return {
      label: "Configuration invalid",
      detail: "The dashboard’s Signal API address is invalid, so it is not connecting.",
      tone: "warning",
      icon: Attention,
    };
  }
  if (snapshot.connection === "unreachable") {
    return {
      label: "API unavailable",
      detail: "The dashboard could not reach the Signal server.",
      tone: "warning",
      icon: Unavailable,
    };
  }
  return {
    label:
      snapshot.inventory === "available" ? "Connected" : "Response rejected",
    detail:
      snapshot.inventory === "available"
        ? "The dashboard verified the public capability contract."
        : "The control plane returned an invalid capability contract.",
    tone: snapshot.inventory === "available" ? "positive" : "warning",
    icon: snapshot.inventory === "available" ? Verified : Attention,
  };
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "at an unknown time";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date);
}

function selectedSite(
  session: DashboardSession,
  sites: DashboardSiteDirectory,
) {
  if (
    session.state !== "authenticated" ||
    sites.state !== "available" ||
    session.activeSiteId === null
  ) {
    return null;
  }
  return sites.sites.find((site) => site.id === session.activeSiteId) ?? null;
}
