import type { WeeklyDeliveryItem, WeeklyReportState } from "../lib/weekly-report-api";
import { ChangeMeasurements } from "./change-measurements";
import { OUTCOME_LABELS, REPORT_STATUS, STAGE_LABELS, formatDay, readableCode } from "./home-panels";

const skillNames: Record<string, string> = {
  import_gsc: "Search Console", import_bing: "Bing", import_ga4: "Google Analytics",
  brain_refresh: "Business facts", strategy_rebuild: "Strategy", internal_link_proposals: "Internal links", brief_proposals: "Brief proposals",
  report_delivery: "Email report",
  pagespeed_refresh: "Page speed", visibility_reobserve: "AI answers", chat_report_delivery: "Chat report",
};

function evidenceDestination(ref: string, delivery: WeeklyDeliveryItem[]): string | null {
  const operation = delivery.find(item => item.operation_id !== null && ref === `operation:${item.operation_id}`);
  if (operation) return `/changes#operation-${operation.operation_id}`;
  const revision = delivery.find(item => item.revision_sha256 !== null &&
    [
      `revision:${item.revision_sha256}`,
      `finding:${item.finding_id}`,
      `recipe:${item.recipe_release_id}`,
    ].includes(ref));
  return revision ? `/approvals?revision=${revision.revision_id}#candidate-inbox-title` : null;
}

export function WeeklyReport({ value }: { value: WeeklyReportState }) {
  if (value.state === "rejected") return null;
  return <section className="weekly-cycle-report" aria-labelledby="weekly-report-title">
    <header className="weekly-cycle-header"><h2 id="weekly-report-title">Weekly report</h2></header>
    {value.state === "empty" ? <p>No recorded cycle.</p> : value.state === "unavailable" ?
      <p role="status">Weekly cycle evidence unavailable.</p> : <>
        <p>Week of {formatDay(value.report.weekStart)} · {REPORT_STATUS[value.report.status] ?? readableCode(value.report.status)}</p>
        {value.report.skills.length > 0 && <section aria-labelledby="weekly-skills-title">
          <h3 id="weekly-skills-title">Employee skills</h3>
          <dl className="change-scope">{value.report.skills.map(skill => <div key={skill.stage}>
            <dt>{skillNames[skill.stage]}</dt>
            <dd><strong>{OUTCOME_LABELS[skill.outcome] ?? readableCode(skill.outcome)}</strong>: {readableCode(skill.detailCode).toLowerCase()}
              <span style={{ display: "block" }}>Reserved budget: ${(skill.reservedCents / 100).toFixed(2)} · {skill.units} {skill.units === 1 ? "unit" : "units"}</span>
              {skill.spendStatus === "upper_bound_reserved" && <span style={{ display: "block" }}>Actual provider spend unreported.</span>}
              {skill.evidenceRefs.length > 0 && <details className="technical-details"><summary>Technical details</summary>
                {skill.evidenceRefs.map(ref => <code key={ref} style={{ display: "block", overflowWrap: "anywhere" }}>{ref}</code>)}
              </details>}
            </dd>
          </div>)}</dl>
        </section>}
        <dl className="change-scope">{value.report.stages.map(stage => <div key={stage.stage}>
          <dt>{STAGE_LABELS[stage.stage] ?? readableCode(stage.stage)}</dt><dd>{OUTCOME_LABELS[stage.outcome] ?? readableCode(stage.outcome)} · {readableCode(stage.detailCode)}
            {stage.evidenceRefs.length > 0 && <details className="technical-details"><summary>Evidence</summary>{stage.evidenceRefs.map(ref => {
              const destination = evidenceDestination(ref, value.report.delivery);
              return destination ? <a key={ref} href={destination} style={{ display: "block", overflowWrap: "anywhere" }}><code>{ref}</code></a> : <code key={ref} style={{ display: "block", overflowWrap: "anywhere" }}>{ref}</code>;
            })}</details>}
          </dd>
        </div>)}</dl>
        <h3>What shipped</h3>
        {value.report.delivery.length === 0 && <p>No delivery records in this cycle.</p>}
        {value.report.delivery.map(item => <article className="approval-section" key={item.workload_id} aria-label="Candidate delivery">
          <dl className="change-scope">
            <div><dt>Revision</dt><dd><code style={{ overflowWrap: "anywhere" }}>{item.revision_sha256 ?? "Candidate not sealed"}</code></dd></div>
            <div><dt>Delivery</dt><dd>{item.delivery_outcome === "verified" ? "Live verified" : (item.delivery_stage ? readableCode(item.delivery_stage) : null) ?? (item.operation_state ? readableCode(item.operation_state) : null) ?? (item.revision_sha256 ? "Inbox review required" : "Candidate unavailable")}</dd></div>
            <div><dt>Authorization</dt><dd>{item.authority_kind === "standing_grant" ? "Standing authorization" : item.authority_kind === "owner_inbox" ? "Owner Inbox approval" : "Not authorized"}</dd></div>
            {item.decision_channel && <div><dt>Decision channel</dt><dd>{item.decision_channel === "slack" ? "Slack" : item.decision_channel === "telegram" ? "Telegram" : "Dashboard"}</dd></div>}
            {item.delivery_reason && <div><dt>Evidence outcome</dt><dd>{readableCode(item.delivery_reason)}</dd></div>}
          </dl>
          {item.pr_url && <a href={item.pr_url} target="_blank" rel="noreferrer">Open pull request</a>}
          {item.revision_sha256 && <a href={`/approvals?revision=${item.revision_id}#candidate-inbox-title`}>{item.review_status === null && item.operation_id === null ? "Review exact revision" : "Revision evidence"}</a>}
          {item.observation_sha256 && <p><code style={{ overflowWrap: "anywhere" }}>Observation: {item.observation_sha256}</code></p>}
        </article>)}
        <ChangeMeasurements values={value.report.measurements} />
        <section aria-labelledby="report-next-title"><h3 id="report-next-title">What is next</h3>
          {!value.report.next || value.report.next.state === "unavailable" ? <p>Strategy or backlog unavailable.</p> : <ul>{value.report.next.items.map(item => <li key={item.revision_sha256}>Week of {formatDay(item.next_week)} · {readableCode(item.reason)}<details className="technical-details"><summary>Technical details</summary><code style={{ display: "block", overflowWrap: "anywhere" }}>{item.revision_sha256}</code></details></li>)}</ul>}
        </section>
        <section aria-labelledby="report-decisions-title"><h3 id="report-decisions-title">Needs a decision</h3>
          {value.report.decisions ? <><p>Current <a href="/approvals">Inbox</a>: {value.report.decisions.count} pending</p><ul>{value.report.decisions.items.map(item => <li key={item.revision_id}><a href={item.url}>{item.kind === "editorial" ? "Content review" : "Review exact revision"}</a></li>)}</ul></> : <p>Inbox count unavailable.</p>}
        </section>
      </>}
  </section>;
}
