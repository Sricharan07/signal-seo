import type { VisibilityData } from "./ai-visibility-api";
import type { DashboardCandidateRevision } from "./candidate-inbox-api";
import type { ChangeMeasurement } from "./change-measurement-api";
import type { DashboardDeliveryObservation } from "./github-delivery-api";
import type { DashboardGithubPrOperation } from "./github-pr-operations-api";
import type { SeoProjection } from "./seo-strategy-api";

/* Pure derivations for the owner views. Every value is computed from recorded
   evidence; anything that cannot be computed is null and the view says so. */

export type SearchMetric = "clicks" | "impressions" | "position";
export interface DailyPoint { date: string; value: number }

const DAY = 86_400_000;
const dayOf = (iso: string) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);

/** One value per date from the exact date-only cohort of one source. Sources are never combined. */
export function dailySeries(projection: SeoProjection | null, metric: SearchMetric, source: "gsc" | "bing" = "gsc"): DailyPoint[] {
  const cohort = projection?.snapshot?.payload.performance.find(
    (item) => item.source === source && item.dimensions.length === 1 && item.dimensions[0] === "date",
  );
  if (!cohort) return [];
  const values = new Map<string, number>();
  for (const row of cohort.rows) {
    const date = row.labels.date;
    const value = row.metrics[metric]?.value;
    if (date && /^\d{4}-\d{2}-\d{2}$/.test(date) && typeof value === "number" && Number.isFinite(value)) values.set(date, value);
  }
  return [...values].sort(([a], [b]) => a.localeCompare(b)).map(([date, value]) => ({ date, value }));
}

const contiguous = (points: DailyPoint[]) => points.every((point, index) => index === 0 || dayOf(point.date) - dayOf(points[index - 1].date) === DAY);

export interface WindowComparison { current: number; previous: number | null; change: number | null; days: number }

/** The last `days` returned days against the `days` before them. A comparison needs two complete,
    gap-free windows; otherwise only the current window is reported. */
export function compareWindows(series: DailyPoint[], kind: "sum" | "mean", days = 28): WindowComparison | null {
  if (series.length === 0) return null;
  const aggregate = (points: DailyPoint[]) => {
    const total = points.reduce((sum, point) => sum + point.value, 0);
    return kind === "sum" ? total : total / points.length;
  };
  const current = series.slice(-days);
  const both = series.slice(-days * 2);
  if (series.length < days * 2 || !contiguous(both)) {
    return { current: aggregate(current), previous: null, change: null, days: current.length };
  }
  const previous = aggregate(both.slice(0, days));
  const now = aggregate(both.slice(days));
  return { current: now, previous, change: previous === 0 ? null : (now - previous) / previous, days };
}

export function percent(change: number | null): string | null {
  if (change === null) return null;
  const rounded = Math.round(change * 100);
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

/** "Clicks up 18% in 28 days." or null when there is no full comparison. */
export function clicksHeadline(comparison: WindowComparison | null): string | null {
  if (!comparison || comparison.change === null) return null;
  const rounded = Math.round(comparison.change * 100);
  if (rounded >= 1) return `Clicks up ${rounded}% in 28 days.`;
  if (rounded <= -1) return `Clicks down ${Math.abs(rounded)}% in 28 days.`;
  return "Clicks held steady in 28 days.";
}

export function compactNumber(value: number): string {
  if (value >= 10_000) return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
  return new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(value);
}

/** Sparkline paths in a 120 x 40 box, as drawn in the tiles. */
export function sparkPaths(values: number[], width = 120, height = 40): { line: string; area: string } | null {
  if (values.length < 2) return null;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const line = values.map((value, index) =>
    `${index ? "L" : "M"}${((index / (values.length - 1)) * width).toFixed(1)} ${(height - 4 - ((value - min) / span) * (height - 10)).toFixed(1)}`).join(" ");
  return { line, area: `${line} L${width} ${height} L0 ${height} Z` };
}

export interface ChartGeometry {
  line: string; area: string;
  ticks: { top: number; label: string }[];
  xLabels: { left: number; label: string; edge: "start" | "middle" | "end" }[];
}

const shortDate = (iso: string) => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${iso.slice(0, 10)}T00:00:00Z`));

/** Chart paths in a 1000 x 300 box with three labelled gridlines and four date labels. */
export function chartGeometry(points: DailyPoint[], invert = false): ChartGeometry | null {
  if (points.length < 2) return null;
  const values = points.map((point) => point.value);
  const max = Math.max(...values), min = invert ? Math.min(...values) : 0;
  const top = niceCeiling(max);
  const span = (top - min) || 1;
  const y = (value: number) => invert ? 20 + ((value - min) / span) * 260 : 280 - ((value - min) / span) * 260;
  const line = points.map((point, index) => `${index ? "L" : "M"}${((index / (points.length - 1)) * 1000).toFixed(1)} ${y(point.value).toFixed(1)}`).join(" ");
  const ticks = [top, min + span / 2, min].map((value) => ({ top: (y(value) / 300) * 100, label: compactNumber(Math.round(value * 10) / 10) }));
  const picks = [0, Math.round((points.length - 1) / 3), Math.round(((points.length - 1) * 2) / 3), points.length - 1];
  const xLabels = picks.map((index, n) => ({
    left: (index / (points.length - 1)) * 100,
    label: shortDate(points[index].date),
    edge: n === 0 ? "start" as const : n === picks.length - 1 ? "end" as const : "middle" as const,
  }));
  return { line, area: `${line} L1000 300 L0 300 Z`, ticks, xLabels };
}

function niceCeiling(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (step * magnitude >= value) return step * magnitude;
  return 10 * magnitude;
}

export interface ChangeMarker { date: string; left: number; title: string; result: string; href: string }

/** Changes Signal confirmed live inside the charted window, placed on its dates. */
export function changeMarkers(
  points: DailyPoint[],
  observations: readonly DashboardDeliveryObservation[],
  operations: readonly DashboardGithubPrOperation[],
  revisions: readonly DashboardCandidateRevision[],
  measurements: readonly ChangeMeasurement[] = [],
): ChangeMarker[] {
  if (points.length < 2) return [];
  const first = dayOf(points[0].date), last = dayOf(points[points.length - 1].date);
  const markers: ChangeMarker[] = [];
  for (const observation of observations) {
    if (observation.state !== "completed" || observation.outcome !== "verified") continue;
    const at = dayOf(observation.observedAt);
    if (at < first || at > last) continue;
    const operation = operations.find((item) => item.operationId === observation.operationId);
    const revision = operation ? revisions.find((item) => item.revisionId === operation.revisionId) : undefined;
    const measured = measurements.filter((item) => item.operation_id === observation.operationId).sort((a, b) => b.horizon - a.horizon)[0];
    markers.push({
      date: observation.observedAt.slice(0, 10),
      left: ((at - first) / (last - first || 1)) * 100,
      title: revision?.finding.title ?? (operation?.prNumber ? `Pull request #${operation.prNumber}` : "Verified change"),
      result: measured ? measurementResult(measured) : "Live and verified",
      href: `/changes#operation-${observation.operationId}`,
    });
  }
  return markers.sort((a, b) => a.left - b.left);
}

/** "+9% clicks, measured at 28 days" from the Search Console page cohort, or the honest state. */
export function measurementResult(measurement: ChangeMeasurement): string {
  const before = "gsc_page" in measurement.baseline ? measurement.baseline.gsc_page.metrics?.clicks : null;
  const after = measurement.observation.post?.gsc_page.metrics?.clicks;
  if (typeof before === "number" && typeof after === "number" && before > 0) {
    const sign = after >= before ? "+" : "";
    return `${sign}${Math.round(((after - before) / before) * 100)}% clicks at ${measurement.horizon} days`;
  }
  if (!("gsc_page" in measurement.baseline)) return `New page: ${typeof after === "number" ? after : "no"} clicks in ${measurement.horizon} days`;
  return `Measured at ${measurement.horizon} days`;
}

export type CitationState = "cited" | "rival" | "nobody" | "unknown";
export interface CitationGrid {
  providers: string[];
  questions: { question: string; cells: CitationState[] }[];
  cited: number; rivals: number; checked: number;
}

/** Latest answer per question and assistant: cites the site, cites a rival, cites nobody, or unknown. */
export function citationGrid(data: VisibilityData | null, maxQuestions = 15): CitationGrid | null {
  if (!data || data.gaps.length === 0) return null;
  const order = data.providers.map((item) => item.provider);
  const seen = new Set<string>();
  for (const gap of data.gaps) for (const observation of gap.observations) seen.add(observation.provider);
  const providers = [...order.filter((provider) => seen.has(provider)), ...[...seen].filter((provider) => !order.includes(provider))];
  if (providers.length === 0) return null;
  let cited = 0, rivals = 0, checked = 0;
  const questions = data.gaps.slice(0, maxQuestions).map((gap) => ({
    question: gap.question,
    cells: providers.map((provider) => {
      const latest = gap.observations.filter((item) => item.provider === provider).sort((a, b) => b.observed_at.localeCompare(a.observed_at))[0];
      if (!latest || latest.site_cited === null) return "unknown" as const;
      checked += 1;
      if (latest.site_cited) { cited += 1; return "cited" as const; }
      if (latest.competitor_pages.length > 0) { rivals += 1; return "rival" as const; }
      return "nobody" as const;
    }),
  }));
  return { providers, questions, cited, rivals, checked };
}

export const PROVIDER_NAMES: Record<string, string> = { openai: "ChatGPT", perplexity: "Perplexity", gemini: "Gemini", copilot: "Copilot" };

export function isoWeekLabel(iso: string): string {
  const start = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  const end = new Date(start.getTime() + 6 * DAY);
  const thursday = new Date(start.getTime() + ((3 - ((start.getUTCDay() + 6) % 7)) * DAY));
  const yearStart = Date.UTC(thursday.getUTCFullYear(), 0, 1);
  const week = Math.ceil(((thursday.getTime() - yearStart) / DAY + 1) / 7);
  const format = (date: Date) => new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(date);
  return `Week ${week} · ${format(start)} – ${format(end)}`;
}

export type WorkStage = "research" | "plan" | "write" | "ship" | "verify" | "measure";
export type WorkTone = "you" | "good" | "warn" | "busy" | "plain";
export interface WorkItem { stage: WorkStage; title: string; status: string; tone: WorkTone; who: "You" | "Signal"; when: string; href?: string }

export const WORK_STAGES: { key: WorkStage; label: string }[] = [
  { key: "research", label: "Research" }, { key: "plan", label: "Plan" }, { key: "write", label: "Write" },
  { key: "ship", label: "Ship" }, { key: "verify", label: "Verify" }, { key: "measure", label: "Measure" },
];

interface WriterLike {
  briefs: { brief_id: string; status: string; created_at: string; payload: { topic: string } }[];
  drafts: { draft_id: string; brief_id: string; created_at: string; result: { state: string } }[];
  candidates: { candidate_id: string; draft_id: string; review_status: string; created_at: string; manifest: { changed_files: { path: string }[] } }[];
}

export interface WorkInput {
  projection: SeoProjection | null;
  writer: WriterLike | null;
  revisions: readonly DashboardCandidateRevision[];
  operations: readonly DashboardGithubPrOperation[];
  observations: readonly DashboardDeliveryObservation[];
  measurements: readonly ChangeMeasurement[];
}

/** Everything Signal is working on, placed in the stage it is in now. Only recorded work appears. */
export function buildWorkItems(input: WorkInput): WorkItem[] {
  const items: WorkItem[] = [];
  const snapshot = input.projection?.snapshot ?? null;
  const decisions = input.projection?.decisions ?? [];
  if (snapshot) {
    const open = snapshot.payload.strategy.items.filter((item) => !decisions.some((decision) => decision.item_id === item.id));
    for (const item of [...open].sort((a, b) => b.priority.score - a.priority.score).slice(0, 4)) {
      items.push({ stage: "research", title: `Opportunity: ${item.title}`, status: "Found", tone: "plain", who: "Signal", when: snapshot.created_at, href: "/strategy" });
    }
    for (const decision of decisions.filter((item) => item.decision === "accepted")) {
      const item = snapshot.payload.strategy.items.find((entry) => entry.id === decision.item_id);
      if (item) items.push({ stage: "plan", title: item.title, status: "Accepted by you", tone: "good", who: "You", when: snapshot.created_at, href: "/strategy" });
    }
  }
  for (const brief of input.writer?.briefs ?? []) {
    if (brief.status === "proposed") items.push({ stage: "plan", title: `Brief: ${brief.payload.topic}`, status: "Waiting on you", tone: "you", who: "You", when: brief.created_at, href: "/content-writer" });
    else if (brief.status === "accepted") items.push({ stage: "plan", title: `Brief: ${brief.payload.topic}`, status: "Approved", tone: "good", who: "You", when: brief.created_at, href: "/content-writer" });
  }
  for (const draft of input.writer?.drafts ?? []) {
    if (input.writer?.candidates.some((candidate) => candidate.draft_id === draft.draft_id)) continue;
    const brief = input.writer?.briefs.find((item) => item.brief_id === draft.brief_id);
    items.push({ stage: "write", title: `Article: ${brief?.payload.topic ?? "Untitled draft"}`, status: draft.result.state === "owner_required" ? "Needs your review" : "Drafted", tone: draft.result.state === "owner_required" ? "you" : "busy", who: "Signal", when: draft.created_at, href: "/content-writer" });
  }
  for (const candidate of input.writer?.candidates ?? []) {
    const draft = input.writer?.drafts.find((item) => item.draft_id === candidate.draft_id);
    const brief = draft ? input.writer?.briefs.find((item) => item.brief_id === draft.brief_id) : undefined;
    const title = `Article: ${brief?.payload.topic ?? candidate.manifest.changed_files[0]?.path ?? "New page"}`;
    if (candidate.review_status === "pending") items.push({ stage: "write", title, status: "Waiting on you", tone: "you", who: "You", when: candidate.created_at, href: `/approvals?article=${candidate.candidate_id}` });
  }
  for (const revision of input.revisions) {
    const operation = input.operations.find((item) => item.revisionId === revision.revisionId && item.revisionSha256 === revision.revisionSha256);
    const observation = operation ? latestObservation(input.observations, operation.operationId) : undefined;
    const title = `Fix: ${revision.finding.title}`;
    if (!operation) {
      if (revision.reviewStatus === "pending") items.push({ stage: "ship", title, status: "Waiting on you", tone: "you", who: "You", when: revision.sealedAt, href: `/approvals?revision=${revision.revisionId}` });
      else if (revision.reviewStatus === "approved") items.push({ stage: "ship", title, status: "Approved by you", tone: "good", who: "You", when: revision.decidedAt ?? revision.sealedAt });
      continue;
    }
    if (operation.state === "dispatching" || operation.state === "outcome_unknown") { items.push({ stage: "ship", title, status: "Outcome unknown", tone: "warn", who: "Signal", when: operation.updatedAt, href: `/changes#operation-${operation.operationId}` }); continue; }
    if (operation.state !== "opened") { items.push({ stage: "ship", title, status: "Preparing the pull request", tone: "busy", who: "Signal", when: operation.updatedAt }); continue; }
    const pr = operation.prNumber ? `, pull request #${operation.prNumber}` : "";
    if (observation?.state === "completed" && observation.outcome === "verified") {
      const measured = input.measurements.filter((item) => item.operation_id === operation.operationId).sort((a, b) => b.horizon - a.horizon)[0];
      if (measured) items.push({ stage: "measure", title: revision.finding.title, status: measurementResult(measured), tone: "good", who: "Signal", when: measured.recorded_at ?? measured.due_at, href: `/changes#operation-${operation.operationId}` });
      else items.push({ stage: "verify", title: `${revision.finding.title}${pr}`, status: "Live and verified", tone: "good", who: "Signal", when: observation.observedAt, href: `/changes#operation-${operation.operationId}` });
    } else if (observation?.state === "completed" && (observation.outcome === "inconclusive" || observation.outcome === "regressed")) {
      items.push({ stage: "verify", title: `${revision.finding.title}${pr}`, status: "Needs your review", tone: "warn", who: "You", when: observation.observedAt, href: `/changes#operation-${operation.operationId}` });
    } else if (observation?.state === "completed" && observation.outcome === "not_yet_deployed") {
      items.push({ stage: "verify", title: `${revision.finding.title}${pr}`, status: "Waiting for your deploy", tone: "warn", who: "You", when: observation.observedAt, href: `/changes#operation-${operation.operationId}` });
    } else {
      items.push({ stage: "ship", title: `${revision.finding.title}${pr}`, status: "Waiting for your merge", tone: "warn", who: "You", when: operation.updatedAt, href: operation.prUrl ?? `/changes#operation-${operation.operationId}` });
    }
  }
  return items;
}

function latestObservation(observations: readonly DashboardDeliveryObservation[], operationId: string) {
  return observations.filter((item) => item.operationId === operationId).sort((a, b) => b.observedAt.localeCompare(a.observedAt))[0];
}

export interface JobRow { label: string; value: string | null }
export interface Job { label: string; title: string; rows: JobRow[] }

export interface JobInput extends WorkInput {
  visibility: VisibilityData | null;
  pagesCrawled: number | null;
  lastAudit: string | null;
  reportStatus: string | null;
  shipped: number | null;
}

/** The five jobs Signal does, each with the counts it has actually recorded. Null means not observed. */
export function buildJobs(input: JobInput): Job[] {
  const clicks = compareWindows(dailySeries(input.projection, "clicks"), "sum");
  const bing = compareWindows(dailySeries(input.projection, "clicks", "bing"), "sum");
  const items = input.projection?.snapshot?.payload.strategy.items ?? null;
  const grid = citationGrid(input.visibility);
  const writer = input.writer;
  const opened = input.operations.filter((item) => item.state === "opened");
  const verified = input.observations.filter((item) => item.state === "completed" && item.outcome === "verified");
  const n = (value: number | null | undefined, unit = "") => value === null || value === undefined ? null : `${new Intl.NumberFormat("en").format(Math.round(value))}${unit}`;
  return [
    { label: "Research", title: "Reading what changed in your search data this week.", rows: [
      { label: "Search Console", value: clicks ? `${n(clicks.current)} clicks` : null },
      { label: "Bing", value: bing ? `${n(bing.current)} clicks` : null },
      { label: "Pages crawled", value: n(input.pagesCrawled) },
      { label: "Last site audit", value: input.lastAudit },
      { label: "Opportunities", value: items ? `${items.length} found` : null },
    ] },
    { label: "Writing", title: "Writing what your site is missing, in your voice.", rows: [
      { label: "Briefs approved", value: writer ? n(writer.briefs.filter((item) => item.status === "accepted").length) : null },
      { label: "Drafts", value: writer ? n(writer.drafts.length) : null },
      { label: "Sent to you", value: writer ? `${writer.candidates.filter((item) => item.review_status === "pending").length} waiting` : null },
    ] },
    { label: "Technical", title: "Fixing what stops search engines, through pull requests.", rows: [
      { label: "Fixes waiting on you", value: n(input.revisions.filter((item) => item.reviewStatus === "pending").length) },
      { label: "Pull requests open", value: n(opened.length) },
      { label: "Live and verified", value: n(verified.length) },
    ] },
    { label: "AI answers", title: "Asking AI assistants your buyers’ questions.", rows: [
      { label: "Questions", value: input.visibility ? n(input.visibility.gaps.length) : null },
      { label: "Answers checked", value: grid ? n(grid.checked) : null },
      { label: "Citing you", value: grid ? n(grid.cited) : null },
      { label: "Citing rivals", value: grid ? n(grid.rivals) : null },
    ] },
    { label: "Report", title: "One brief a week: what changed and what Signal did.", rows: [
      { label: "This week’s report", value: input.reportStatus },
      { label: "Shipped", value: input.shipped === null ? null : `${input.shipped} ${input.shipped === 1 ? "change" : "changes"}` },
      { label: "Measured", value: n(input.measurements.length) },
    ] },
  ];
}

export type ActivityKind = "shipped" | "drafted" | "decided" | "measured" | "held";
export interface ActivityEntry { at: string; exactTime: boolean; kind: ActivityKind; text: string; authority: string; status: string; tone: WorkTone; href?: string }

export interface ActivityInput extends WorkInput {
  report: { weekStart: string; stages: { stage: string; outcome: string; detailCode: string }[]; skills: { stage: string; outcome: string; detailCode: string }[] } | null;
}

const AUTHORITY_WORDS: Record<string, string> = {
  standing_grant: "On its own, within your standing approval",
  owner_editorial: "Approved by you as an article",
  owner_inbox: "Approved by you in the Inbox",
};
const CHANNEL_WORDS: Record<string, string> = { slack: "in Slack", telegram: "in Telegram", dashboard: "in the dashboard" };
const STAGE_WORDS: Record<string, string> = { observe: "Observing", analyze: "Analysis", plan: "Planning", prepare: "Preparing changes", gate: "The check before shipping", handoff: "Handing off", verify: "Verifying", measure: "Measuring", report: "The weekly report" };

const sentence = (code: string) => {
  const words = code.replace(/[_\s]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/** Every recorded thing Signal did or was stopped from doing, newest first. */
export function buildActivity(input: ActivityInput): ActivityEntry[] {
  const entries: ActivityEntry[] = [];
  const titleOf = (revisionId: string) => input.revisions.find((item) => item.revisionId === revisionId)?.finding.title ?? "a change";
  for (const revision of input.revisions) {
    entries.push({ at: revision.sealedAt, exactTime: true, kind: "drafted", text: `Prepared a fix: ${revision.finding.title}.`, authority: revision.approvalClass === "A4" ? "Big changes always come to you" : "Fixes come to you unless your standing approval covers them", status: revision.reviewStatus === "pending" ? "In your Inbox" : "Prepared", tone: revision.reviewStatus === "pending" ? "you" : "plain", href: `/approvals?revision=${revision.revisionId}` });
    if (revision.decidedAt && revision.reviewStatus !== "pending") {
      const decision = { approved: "approved", rejected: "rejected", changes_requested: "asked for changes to", superseded: "replaced", stale_base: "left behind" }[revision.reviewStatus] ?? "decided on";
      entries.push({ at: revision.decidedAt, exactTime: true, kind: "decided", text: `You ${decision} the fix: ${revision.finding.title}.`, authority: "Your decision", status: sentence(revision.reviewStatus), tone: revision.reviewStatus === "approved" ? "good" : "plain", href: `/approvals?revision=${revision.revisionId}` });
    }
  }
  for (const operation of input.operations) {
    if (operation.state !== "opened") continue;
    const authority = operation.authority ? `${AUTHORITY_WORDS[operation.authority.kind] ?? "Authorized"}${operation.authority.kind !== "standing_grant" && operation.authority.decisionChannel ? ` ${CHANNEL_WORDS[operation.authority.decisionChannel] ?? ""}` : ""}` : "Authorization recorded";
    const verified = input.observations.some((item) => item.operationId === operation.operationId && item.state === "completed" && item.outcome === "verified");
    entries.push({ at: operation.createdAt, exactTime: true, kind: "shipped", text: `Opened ${operation.prNumber ? `pull request #${operation.prNumber}` : "a pull request"}: ${operation.authority?.kind === "owner_editorial" ? "a new article" : titleOf(operation.revisionId)}.`, authority, status: verified ? "Live and verified" : "Waiting for your merge", tone: verified ? "good" : "warn", href: operation.prUrl ?? `/changes#operation-${operation.operationId}` });
  }
  for (const observation of input.observations) {
    if (observation.state !== "completed" || observation.outcome !== "verified") continue;
    const operation = input.operations.find((item) => item.operationId === observation.operationId);
    entries.push({ at: observation.observedAt, exactTime: true, kind: "shipped", text: `Confirmed ${operation ? titleOf(operation.revisionId).toLowerCase() : "a change"} is live${operation?.prNumber ? ` (pull request #${operation.prNumber})` : ""}.`, authority: "Merged and deployed by you", status: "Live and verified", tone: "good", href: `/changes#operation-${observation.operationId}` });
  }
  for (const measurement of input.measurements) {
    entries.push({ at: measurement.recorded_at ?? measurement.due_at, exactTime: measurement.recorded_at !== null, kind: "measured", text: `Measured ${measurement.page_url} ${measurement.horizon} days after it went live.`, authority: "Observed on your site, not proof of cause", status: measurementResult(measurement), tone: "good", href: `/changes#operation-${measurement.operation_id}` });
  }
  const writer = input.writer;
  for (const draft of writer?.drafts ?? []) {
    const topic = writer?.briefs.find((item) => item.brief_id === draft.brief_id)?.payload.topic ?? "an article";
    entries.push({ at: draft.created_at, exactTime: true, kind: "drafted", text: `Wrote a draft of “${topic}” from a brief you approved.`, authority: "Articles always come to you", status: draft.result.state === "owner_required" ? "Needs your review" : sentence(draft.result.state), tone: draft.result.state === "owner_required" ? "you" : "busy", href: "/content-writer" });
  }
  for (const candidate of writer?.candidates ?? []) {
    const draft = writer?.drafts.find((item) => item.draft_id === candidate.draft_id);
    const topic = writer?.briefs.find((item) => item.brief_id === draft?.brief_id)?.payload.topic ?? "an article";
    entries.push({ at: candidate.created_at, exactTime: true, kind: "drafted", text: `Sent “${topic}” to you for review.`, authority: "Articles always come to you", status: candidate.review_status === "pending" ? "In your Inbox" : sentence(candidate.review_status), tone: candidate.review_status === "pending" ? "you" : "plain", href: `/approvals?article=${candidate.candidate_id}` });
  }
  if (input.report) {
    const day = `${input.report.weekStart}T00:00:00Z`;
    for (const stage of input.report.stages) {
      if (!["waiting_owner", "stopped", "deferred", "failed"].includes(stage.outcome)) continue;
      entries.push({ at: day, exactTime: false, kind: "held", text: `${STAGE_WORDS[stage.stage] ?? sentence(stage.stage)} held back: ${sentence(stage.detailCode).toLowerCase()}.`, authority: stage.outcome === "waiting_owner" ? "Waiting for your decision" : "Stopped by Signal’s own check", status: sentence(stage.outcome), tone: stage.outcome === "waiting_owner" ? "you" : "warn" });
    }
    const report = input.report.skills.find((skill) => skill.stage === "report_delivery");
    if (report && report.outcome === "completed") entries.push({ at: day, exactTime: false, kind: "shipped", text: "Sent your weekly report.", authority: "Every week", status: "Sent", tone: "plain" });
  }
  return entries.sort((a, b) => b.at.localeCompare(a.at));
}

export interface ChangeRow { title: string; live: string; beforeAfter: string; effect: string | null; effectTone: "up" | "down" | "flat"; status: string; href: string }

/** One row per measured change: its latest horizon, Search Console page clicks before and after. */
export function buildChangeRows(
  measurements: readonly ChangeMeasurement[],
  operations: readonly DashboardGithubPrOperation[],
  revisions: readonly DashboardCandidateRevision[],
): ChangeRow[] {
  const latest = new Map<string, ChangeMeasurement>();
  for (const measurement of measurements) {
    const current = latest.get(measurement.operation_id);
    if (!current || measurement.horizon > current.horizon) latest.set(measurement.operation_id, measurement);
  }
  return [...latest.values()].sort((a, b) => b.verified_live_at.localeCompare(a.verified_live_at)).map((measurement) => {
    const operation = operations.find((item) => item.operationId === measurement.operation_id);
    const revision = operation ? revisions.find((item) => item.revisionId === operation.revisionId) : undefined;
    const newPage = !("gsc_page" in measurement.baseline);
    const before = newPage ? null : (measurement.baseline as { gsc_page: { metrics: { clicks: number | null } | null } }).gsc_page.metrics?.clicks ?? null;
    const after = measurement.observation.post?.gsc_page.metrics?.clicks ?? null;
    const change = before !== null && after !== null && before > 0 ? (after - before) / before : null;
    const rounded = change === null ? null : Math.round(change * 100);
    let path = measurement.page_url;
    try { path = new URL(measurement.page_url).pathname; } catch { /* keep the recorded URL */ }
    return {
      title: revision?.finding.title ?? `Change to ${path}`,
      live: new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(measurement.verified_live_at)),
      beforeAfter: newPage ? `New page → ${after ?? "?"}` : `${before ?? "?"} → ${after ?? "?"}`,
      effect: rounded === null ? null : `${rounded > 0 ? "+" : ""}${rounded}%`,
      effectTone: rounded === null || rounded === 0 ? "flat" : rounded > 0 ? "up" : "down",
      status: measurement.observation.state === "measured_as_reported" ? `Measured, ${measurement.horizon} days` : sentence(measurement.observation.state),
      href: `/changes#operation-${measurement.operation_id}`,
    };
  });
}
