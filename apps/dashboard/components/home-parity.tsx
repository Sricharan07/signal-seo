import type { ReactNode } from "react";
import { ArrowRight } from "lucide-react";

import type { DashboardCandidateInbox } from "../lib/candidate-inbox-api";
import type { DashboardDeliveryObservations } from "../lib/github-delivery-api";
import type { DashboardGithubPrOperations } from "../lib/github-pr-operations-api";
import {
  PROVIDER_NAMES, WORK_STAGES, buildJobs, buildWorkItems, changeMarkers, chartGeometry, citationGrid, clicksHeadline,
  compactNumber, compareWindows, dailySeries, isoWeekLabel, percent, sparkPaths, type WindowComparison,
} from "../lib/insights";
import type { OwnerInsights } from "../lib/owner-insights";
import type { DashboardWork } from "../lib/work-api";
import type { WeeklyReportState } from "../lib/weekly-report-api";
import { CitationsGrid, SearchChart, SignalRightNow, StageLoop } from "./home-insights";
import { REPORT_STATUS, readableCode } from "./home-panels";

/* Home, built from recorded evidence only. Prototype numbers are never used:
   a tile, chart or row without observed data says so in words. */

export interface WaitingItem { kind: "Fix" | "Article" | "Fact"; title: string; why: string; href: string }

export function waitingItems(candidateInbox: DashboardCandidateInbox, insights: OwnerInsights): WaitingItem[] {
  const items: WaitingItem[] = [];
  if (candidateInbox.state === "available") {
    for (const revision of candidateInbox.revisions.filter((item) => item.reviewStatus === "pending")) {
      items.push({ kind: "Fix", title: revision.finding.title, why: revision.finding.summary, href: `/approvals?revision=${revision.revisionId}` });
    }
  }
  if (insights.writer.state === "available") {
    const writer = insights.writer.value;
    for (const candidate of writer.candidates.filter((item) => item.review_status === "pending")) {
      const draft = writer.drafts.find((item) => item.draft_id === candidate.draft_id);
      const brief = draft ? writer.briefs.find((item) => item.brief_id === draft.brief_id) : undefined;
      items.push({ kind: "Article", title: brief ? `New article: ${brief.payload.topic}` : "New article", why: "Written from your approved facts. It needs your review.", href: `/approvals?article=${candidate.candidate_id}` });
    }
  }
  if (insights.facts.state === "available") {
    for (const fact of insights.facts.value.filter((item) => item.status === "proposed").slice(0, 10)) {
      items.push({ kind: "Fact", title: `Confirm “${fact.statement}”`, why: "Signal only writes facts you confirm.", href: `/approvals?fact=${fact.fact_id}` });
    }
  }
  return items;
}

const shortDate = (iso: string) => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));

function weekStartOf(iso: string): string {
  const date = new Date(iso);
  const monday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() - ((date.getUTCDay() + 6) % 7)));
  return monday.toISOString().slice(0, 10);
}

export function HomeParityHeader({ fetchedAt, weeklyReport, insights, waiting }: {
  fetchedAt: string;
  weeklyReport: WeeklyReportState;
  insights: OwnerInsights;
  waiting: number;
}) {
  const projection = insights.seo.state === "available" ? insights.seo.value : null;
  const clicks = clicksHeadline(compareWindows(dailySeries(projection, "clicks"), "sum"));
  const decisions = waiting === 0 ? "Nothing waits on you." : `${waiting} ${waiting === 1 ? "decision waits" : "decisions wait"} on you.`;
  const week = weeklyReport.state === "available" ? weeklyReport.report.weekStart : weekStartOf(fetchedAt);
  return (
    <header className="c-ph">
      <div>
        <span className="c-ml">{isoWeekLabel(week)}</span>
        <h1 className="c-h1">
          <span className="c-ln"><span style={{ animationDelay: ".05s" }}>{clicks ?? decisions}</span></span>
          <span className="c-ln"><span className="c-soft" style={{ animationDelay: ".15s" }}>{clicks ? decisions : "Here is what Signal did and what it needs from you."}</span></span>
        </h1>
      </div>
      <div className="c-acts">
        <a className="secondary-command" href="#ask">Ask Signal</a>
        {waiting > 0 ? <a className="primary-command" href="/approvals">Review {waiting === 1 ? "1 decision" : `${waiting} decisions`} <ArrowRight size={16} aria-hidden="true" /></a> : null}
      </div>
    </header>
  );
}

interface Tile { label: string; value: string | null; delta: string | null; deltaTone: "up" | "down" | "flat"; foot: string; spark: { line: string; area: string } | null; hero?: boolean; href?: string }

function trend(comparison: WindowComparison | null, lowerIsBetter = false): { delta: string | null; tone: Tile["deltaTone"] } {
  if (!comparison || comparison.change === null || comparison.previous === null) return { delta: null, tone: "flat" };
  if (lowerIsBetter) {
    const moved = comparison.previous - comparison.current;
    const rounded = Math.round(Math.abs(moved) * 10) / 10;
    if (rounded === 0) return { delta: "No change", tone: "flat" };
    return { delta: `${rounded} ${moved > 0 ? "better" : "worse"}`, tone: moved > 0 ? "up" : "down" };
  }
  const label = percent(comparison.change);
  return { delta: label, tone: comparison.change > 0.005 ? "up" : comparison.change < -0.005 ? "down" : "flat" };
}

export function MetricTiles({ insights }: { insights: OwnerInsights }) {
  const projection = insights.seo.state === "available" ? insights.seo.value : null;
  const clicksSeries = dailySeries(projection, "clicks");
  const impressionsSeries = dailySeries(projection, "impressions");
  const positionSeries = dailySeries(projection, "position");
  const clicks = compareWindows(clicksSeries, "sum");
  const impressions = compareWindows(impressionsSeries, "sum");
  const position = compareWindows(positionSeries, "mean");
  const grid = citationGrid(insights.visibility.state === "available" ? insights.visibility.value : null);
  const noSearch = insights.seo.state !== "available" ? "Search data could not be read." : "Connect Search Console to see this.";
  const clicksTrend = trend(clicks), impressionsTrend = trend(impressions), positionTrend = trend(position, true);
  const tiles: Tile[] = [
    { hero: true, label: "Clicks · 28 days", value: clicks ? new Intl.NumberFormat("en").format(Math.round(clicks.current)) : null, delta: clicksTrend.delta, deltaTone: clicksTrend.tone,
      foot: clicks ? (clicks.days < 28 ? `From Google Search Console, ${clicks.days} days so far` : "From Google Search Console") : noSearch, spark: sparkPaths(clicksSeries.slice(-28).map((point) => point.value)), href: "/analytics" },
    { label: "Impressions", value: impressions ? compactNumber(Math.round(impressions.current)) : null, delta: impressionsTrend.delta, deltaTone: impressionsTrend.tone,
      foot: impressions ? "Times you appeared in search" : noSearch, spark: sparkPaths(impressionsSeries.slice(-28).map((point) => point.value)), href: "/analytics" },
    { label: "Average position", value: position ? (Math.round(position.current * 10) / 10).toFixed(1) : null, delta: positionTrend.delta, deltaTone: positionTrend.tone,
      foot: position ? "Lower is better" : noSearch, spark: sparkPaths(positionSeries.slice(-28).map((point) => -point.value)), href: "/analytics" },
    { label: "AI answers citing you", value: grid ? String(grid.cited) : null, delta: null, deltaTone: "flat",
      foot: grid ? `Of ${grid.checked} answers checked` : insights.visibility.state === "available" ? "No questions checked yet." : "AI answers could not be read.", spark: null, href: "/visibility" },
  ];
  return (
    <div className="c-tiles">
      {tiles.map((tile, index) => (
        <a key={tile.label} className={`c-tile c-in${tile.hero ? " hero" : ""}`} href={tile.href} style={{ animationDelay: `${0.03 + index * 0.06}s` }}>
          {tile.hero ? <span className="c-lat" aria-hidden="true" /> : null}
          <div className="c-tile-top"><span className="c-ml">{tile.label}</span>{tile.delta ? <span className={`c-dl ${tile.deltaTone}`}>{tile.delta}</span> : null}</div>
          {tile.value === null ? <b className="c-tile-none">No data yet</b> : <b>{tile.value}</b>}
          <small>{tile.foot}</small>
          {tile.spark ? (
            <svg className="c-spark" viewBox="0 0 120 40" preserveAspectRatio="none" aria-hidden="true"><path className="a" d={tile.spark.area} /><path className="l" d={tile.spark.line} /></svg>
          ) : <span className="c-spark-none" aria-hidden="true" />}
        </a>
      ))}
    </div>
  );
}

export function WaitingPanel({ items, inboxReadable }: { items: WaitingItem[]; inboxReadable: boolean }) {
  return (
    <section className="c-pn c-pn-call c-in" aria-label="Waiting on you">
      <div className="c-pn-h"><b>Waiting on you</b>{items.length > 0 ? <span className="c-pill l">{items.length} open</span> : null}</div>
      {!inboxReadable ? (
        <div className="c-wt"><span className="c-wt-m"><b>Your Inbox is unavailable right now</b><span>Decisions could not be read. Nothing was approved or rejected.</span></span></div>
      ) : items.length === 0 ? (
        <div className="c-wt"><span className="c-ok" aria-hidden="true" /><span className="c-wt-m"><b>Nothing waits on you</b><span>Signal asks here, and in Slack or Telegram if connected, when it needs a decision.</span></span></div>
      ) : items.slice(0, 6).map((item, index) => (
        <a key={`${item.href}`} className="c-wt c-rs" href={item.href} style={{ animationDelay: `${0.25 + index * 0.07}s` }}>
          <span className="c-pill o">{item.kind}</span>
          <span className="c-wt-m"><b>{item.title}</b><span>{item.why}</span></span>
          <ArrowRight className="c-go" size={16} aria-hidden="true" />
        </a>
      ))}
    </section>
  );
}

export function HomeParity({ insights, candidateInbox, githubPrOperations, deliveryObservations, weeklyReport, work, fetchedAt, waiting, problems, loopControl }: {
  insights: OwnerInsights;
  candidateInbox: DashboardCandidateInbox;
  githubPrOperations: DashboardGithubPrOperations;
  deliveryObservations: DashboardDeliveryObservations;
  weeklyReport: WeeklyReportState;
  work: DashboardWork;
  fetchedAt: string;
  waiting: WaitingItem[];
  problems: string[];
  loopControl?: ReactNode;
}) {
  const projection = insights.seo.state === "available" ? insights.seo.value : null;
  const visibility = insights.visibility.state === "available" ? insights.visibility.value : null;
  const revisions = candidateInbox.state === "available" ? candidateInbox.revisions : [];
  const operations = githubPrOperations.state === "available" ? githubPrOperations.operations : [];
  const observations = deliveryObservations.state === "available" ? deliveryObservations.observations : [];
  // Without recorded delivery evidence nothing is marked live; say so instead of showing zero.
  const alerts = deliveryObservations.state === "available" || operations.length === 0 ? problems : [...problems, "Delivery evidence unavailable: live checks could not be read"];
  const report = weeklyReport.state === "available" ? weeklyReport.report : null;
  const measurements = report?.measurements ?? [];
  const input = { projection, writer: insights.writer.state === "available" ? insights.writer.value : null, revisions, operations, observations, measurements };
  const items = buildWorkItems(input);
  const series = dailySeries(projection, "clicks").slice(-90);
  const geometry = chartGeometry(series);
  const markers = changeMarkers(series, observations, operations, revisions, measurements);
  const grid = citationGrid(visibility);
  const shipped = report ? report.delivery.filter((item) => item.pr_url !== null || item.delivery_outcome === "verified").length : null;
  const jobs = buildJobs({
    ...input, visibility,
    pagesCrawled: work.state === "available" && work.work.result ? work.work.result.discoveredCount : projection?.snapshot ? projection.snapshot.payload.pages.length : null,
    lastAudit: work.state === "available" && work.work.status === "succeeded" ? `Finished ${shortDate(work.work.projectedAt ?? work.work.acceptedAt)}` : work.state === "available" && ["accepted", "workflow_admitted", "processing"].includes(work.work.status) ? "Running now" : null,
    reportStatus: report ? `Week of ${report.weekStart.slice(5).replace("-", "/")} · ${REPORT_STATUS[report.status] ?? readableCode(report.status)}` : null,
    shipped,
  });
  const working = (work.state === "available" && ["accepted", "workflow_admitted", "processing"].includes(work.work.status)) || report?.status === "running";
  const youCount = items.filter((item) => item.tone === "you").length;
  const updated = new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }).format(new Date(fetchedAt)) + " UTC";
  return (
    <>
      <MetricTiles insights={insights} />
      <div className="c-grid">
        <div className="c-col">
          <StageLoop
            stages={WORK_STAGES}
            items={items}
            note={youCount > 0 ? `${youCount} ${youCount === 1 ? "item waits" : "items wait"} on you · pick a stage to filter` : "Pick a stage to filter"}
            centre={report ? `${REPORT_STATUS[report.status] ?? readableCode(report.status)} this week` : "Not run yet this week"}
            control={loopControl}
          />
          <SearchChart
            title="Clicks from search · last 90 days"
            geometry={geometry}
            markers={markers}
            emptyTitle={projection ? "No daily search data yet" : "Search data unavailable"}
            emptyDetail={projection ? "Connect Search Console and Signal charts clicks here, with a marker for every change it shipped." : "Search data could not be read right now."}
          />
        </div>
        <div className="c-col">
          <WaitingPanel items={waiting} inboxReadable={candidateInbox.state === "available"} />
          <SignalRightNow jobs={jobs} working={working} updated={updated} problems={alerts} />
          {grid ? (
            <CitationsGrid
              title="AI answers citing you"
              summary={`${grid.cited} of ${grid.checked}`}
              providers={grid.providers.map((provider) => PROVIDER_NAMES[provider] ?? readableCode(provider))}
              questions={grid.questions}
            />
          ) : (
            <section className="c-pn c-pn-dark c-in" aria-label="AI answers citing you">
              <div className="c-cbar"><b>AI answers citing you</b></div>
              <div className="c-chart-empty"><b>No questions checked yet</b><span>Approve your buyers’ questions in AI answers and Signal asks the assistants each week.</span><a className="c-link-d" href="/visibility">Open AI answers</a></div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
