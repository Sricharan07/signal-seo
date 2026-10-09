import type { DashboardCandidateInbox } from "../lib/candidate-inbox-api";
import type { DashboardDeliveryObservations } from "../lib/github-delivery-api";
import type { DashboardGithubPrOperations } from "../lib/github-pr-operations-api";
import { PROVIDER_NAMES, buildChangeRows, changeMarkers, chartGeometry, citationGrid, dailySeries } from "../lib/insights";
import type { ChangeMeasurement } from "../lib/change-measurement-api";
import type { OwnerInsights } from "../lib/owner-insights";
import { CitationsGrid, MetricChart } from "./home-insights";
import { readableCode } from "./home-panels";

/* Results: what moved, and which change moved it. Observed values only. */

export function ResultsHeader() {
  return (
    <header className="c-ph">
      <div>
        <span className="c-ml">Results · observed on your site</span>
        <h1 className="c-h1"><span className="c-ln"><span>What moved,</span></span><span className="c-ln"><span className="c-soft" style={{ animationDelay: ".1s" }}>and which change moved it.</span></span></h1>
      </div>
    </header>
  );
}

export function ResultsParity({ insights, candidateInbox, githubPrOperations, deliveryObservations, measurements }: {
  insights: OwnerInsights;
  candidateInbox: DashboardCandidateInbox;
  githubPrOperations: DashboardGithubPrOperations;
  deliveryObservations: DashboardDeliveryObservations;
  measurements: readonly ChangeMeasurement[];
}) {
  const projection = insights.seo.state === "available" ? insights.seo.value : null;
  const revisions = candidateInbox.state === "available" ? candidateInbox.revisions : [];
  const operations = githubPrOperations.state === "available" ? githubPrOperations.operations : [];
  const observations = deliveryObservations.state === "available" ? deliveryObservations.observations : [];
  const series = { clicks: dailySeries(projection, "clicks").slice(-90), impressions: dailySeries(projection, "impressions").slice(-90), position: dailySeries(projection, "position").slice(-90) };
  const charts = { clicks: chartGeometry(series.clicks), impressions: chartGeometry(series.impressions), position: chartGeometry(series.position, true) };
  const markers = changeMarkers(series.clicks, observations, operations, revisions, measurements);
  const rows = buildChangeRows(measurements, operations, revisions);
  const grid = citationGrid(insights.visibility.state === "available" ? insights.visibility.value : null);
  return (
    <>
      <MetricChart charts={charts} markers={markers} />
      <div className="c-grid2">
        <section className="c-pn c-in" style={{ animationDelay: ".1s" }} aria-label="What each change did">
          <div className="c-pn-h"><b>What each change did</b><span className="c-note">Search Console clicks to the page, before and after</span></div>
          {rows.length === 0 ? (
            <p className="c-tb-empty c-results-empty">No change has been measured yet. Signal measures each change 7, 28 and 90 days after it goes live.</p>
          ) : (
            <div className="c-tbl2">
              <div className="c-r2 h" aria-hidden="true"><span>Change</span><span>Live</span><span>Before → after</span><span>Effect</span><span>Status</span></div>
              {rows.map((row, index) => (
                <a key={`${row.href}:${index}`} className="c-r2 c-rs" href={row.href} style={{ animationDelay: `${index * 0.04}s` }}>
                  <span className="c-r2-title">{row.title}</span>
                  <span className="c-mono">{row.live}</span>
                  <span className="c-mono">{row.beforeAfter}</span>
                  <span className={`c-mono c-eff ${row.effectTone}`}>{row.effect ?? "Not comparable"}</span>
                  <span><span className="c-pill g">{row.status}</span></span>
                </a>
              ))}
            </div>
          )}
          <p className="c-results-note">Observed on your site, not proof of cause: seasons and other updates play a part. New pages report their first days instead.</p>
        </section>
        {grid ? (
          <CitationsGrid
            title={`Cited in ${grid.cited} of ${grid.checked} AI answers`}
            summary={`${grid.rivals} lost to rivals`}
            summaryTone="rival"
            providers={grid.providers.map((provider) => PROVIDER_NAMES[provider] ?? readableCode(provider))}
            questions={grid.questions}
            legend
          />
        ) : (
          <section className="c-pn c-pn-dark c-in" aria-label="AI answers">
            <div className="c-cbar"><b>AI answers citing you</b></div>
            <div className="c-chart-empty"><b>No questions checked yet</b><span>Approve your buyers’ questions and Signal asks the assistants each week.</span><a className="c-link-d" href="/visibility">Open AI answers</a></div>
          </section>
        )}
      </div>
    </>
  );
}
