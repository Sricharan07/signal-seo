"use client";

import { useEffect, useId, useState, type ReactNode } from "react";

import type { ChangeMarker, ChartGeometry, CitationState, Job, WorkItem, WorkStage } from "../lib/insights";

/* Interactive parts of the owner views. Every value arrives already computed
   from recorded evidence; these components only filter, reveal and point. */

const STAGE_SPOTS: Record<WorkStage, { x: number; y: number }> = {
  research: { x: 18, y: 16.7 }, plan: { x: 46, y: 16.7 }, write: { x: 76, y: 16.7 },
  ship: { x: 80, y: 83.3 }, verify: { x: 50, y: 83.3 }, measure: { x: 20, y: 83.3 },
};
const TONE_PILL: Record<WorkItem["tone"], string> = { you: "c-pill l", good: "c-pill g", warn: "c-pill w", busy: "c-pill k", plain: "c-pill" };

const shortDay = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.valueOf()) ? "" : new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(date);
};

export function StageLoop({ stages, items, note, centre, control }: {
  stages: { key: WorkStage; label: string }[];
  items: WorkItem[];
  note: string;
  centre: string;
  control?: ReactNode;
}) {
  const [stage, setStage] = useState<WorkStage | null>(null);
  const shown = items.filter((item) => stage === null || item.stage === stage);
  return (
    <section className="c-pn c-in" aria-label="This week’s loop">
      <div className="c-pn-h"><b>This week’s loop</b><span className="c-note">{note}</span></div>
      <div className="c-loop">
        <svg viewBox="0 0 1000 180" preserveAspectRatio="none" aria-hidden="true">
          <path className="lp-base" d="M60 30 H930 Q960 30 960 60 V120 Q960 150 930 150 H70" />
          <path className="lp-ret" d="M70 150 Q40 150 40 120 V60 Q40 30 60 30" />
        </svg>
        <svg className="c-drawin" viewBox="0 0 1000 180" preserveAspectRatio="none" aria-hidden="true"><path className="lp-ink" d="M60 30 H930 Q960 30 960 60 V120 Q960 150 930 150" /></svg>
        <svg className="c-drawinL" viewBox="0 0 1000 180" preserveAspectRatio="none" aria-hidden="true"><path className="lp-ink" d="M930 150 H70" /></svg>
        <div className="c-loop-mid"><span className="c-ml">Every week, in this order</span><b>{centre}</b></div>
        {stages.map(({ key, label }, index) => {
          const own = items.filter((item) => item.stage === key);
          const you = own.some((item) => item.tone === "you");
          const spot = STAGE_SPOTS[key];
          return (
            <button key={key} type="button" className={`c-stg c-popin${stage === key ? " on" : you ? " you" : ""}`}
              style={{ left: `${spot.x}%`, top: `${spot.y}%`, animationDelay: `${0.3 + index * 0.1}s` }}
              aria-pressed={stage === key} aria-label={`${label}: ${own.length} ${own.length === 1 ? "item" : "items"}${you ? ", something waits on you" : ""}`}
              onClick={() => setStage(stage === key ? null : key)}>
              <i aria-hidden="true" />{label}<em>{own.length}</em>
            </button>
          );
        })}
      </div>
      <div className="c-tb" aria-label="Work items">
        <div className="c-tr h" aria-hidden="true"><span>Work</span><span>Stage</span><span>Status</span><span>Who</span><span>Updated</span></div>
        {shown.length === 0 ? (
          <p className="c-tb-empty">{stage === null ? "Nothing is in progress yet. Work appears here as the weekly loop finds it." : "Nothing is in this stage right now."}</p>
        ) : shown.map((item, index) => {
          const row = <>
            <span className="t">{item.title}</span>
            <span className="stgt">{stages.find((entry) => entry.key === item.stage)?.label}</span>
            <span><span className={TONE_PILL[item.tone]}>{item.status}</span></span>
            <span className="who">{item.who}</span>
            <span className="who">{shortDay(item.when)}</span>
          </>;
          return item.href ? (
            <a key={`${item.stage}:${item.title}:${index}`} className="c-tr trb c-rs" href={item.href} style={{ animationDelay: `${0.05 + index * 0.035}s` }}>{row}</a>
          ) : (
            <div key={`${item.stage}:${item.title}:${index}`} className="c-tr c-rs" style={{ animationDelay: `${0.05 + index * 0.035}s` }}>{row}</div>
          );
        })}
      </div>
      {control ? <div className="c-loop-control">{control}</div> : null}
    </section>
  );
}

export function SearchChart({ title, geometry, markers, emptyTitle, emptyDetail, tall = false, children }: {
  title: string;
  geometry: ChartGeometry | null;
  markers: ChangeMarker[];
  emptyTitle: string;
  emptyDetail: string;
  tall?: boolean;
  children?: React.ReactNode;
}) {
  const [tip, setTip] = useState<number | null>(null);
  const fill = `fill-${useId().replace(/:/g, "")}`;
  const active = tip === null ? null : markers[tip] ?? null;
  return (
    <section className="c-pn c-pn-dark c-in" aria-label={title}>
      <span className="c-lat c-lat-d" aria-hidden="true" />
      <div className="c-cbar"><b>{title}</b>{children ?? (markers.length > 0 ? <span className="c-cbar-note">Point at a marker to see the change</span> : null)}</div>
      {geometry === null ? (
        <div className="c-chart-empty"><b>{emptyTitle}</b><span>{emptyDetail}</span></div>
      ) : (
        <>
          <div className={`c-chart${tall ? " tall" : ""}`}>
            {geometry.ticks.map((tick) => <div key={tick.top} className="c-gl" style={{ top: `${tick.top}%` }}><span>{tick.label}</span></div>)}
            <svg className="c-plot c-drawin" viewBox="0 0 1000 300" preserveAspectRatio="none" aria-hidden="true">
              <defs><linearGradient id={fill} x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="st1" /><stop offset="1" className="st2" /></linearGradient></defs>
              <path className="a" fill={`url(#${fill})`} d={geometry.area} />
              <path className="l" d={geometry.line} />
            </svg>
            {markers.map((marker, index) => (
              <a key={`${marker.date}:${index}`} className={`c-mkr${tip === index ? " on" : ""}`} href={marker.href} style={{ left: `${marker.left}%` }}
                onMouseEnter={() => setTip(index)} onMouseLeave={() => setTip(null)} onFocus={() => setTip(index)} onBlur={() => setTip(null)}
                aria-label={`${shortDay(marker.date)}: ${marker.title}. ${marker.result}`}><span /></a>
            ))}
            {active ? (
              <div className="c-tip" style={{ left: `${active.left}%`, translate: active.left > 70 ? "-100% 0" : active.left < 15 ? "0 0" : "-50% 0" }}>
                <span className="c-ml">{shortDay(active.date)}</span><b>{active.title}</b><em>{active.result}</em>
              </div>
            ) : null}
          </div>
          <div className="c-xax">
            {geometry.xLabels.map((label) => (
              <span key={label.left} style={{ left: `${label.left}%`, translate: label.edge === "start" ? "0 0" : label.edge === "end" ? "-100% 0" : "-50% 0" }}>{label.label}</span>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export function SignalRightNow({ jobs, working, updated, problems }: { jobs: Job[]; working: boolean; updated: string; problems: string[] }) {
  const [index, setIndex] = useState(0);
  const job = jobs[index];
  const observed = job.rows.filter((row) => row.value !== null).length;
  return (
    <section className="c-pn c-pn-dark c-in" aria-label="What Signal is doing now">
      <span className="c-lat c-lat-d" aria-hidden="true" />
      <div className="c-cbar">
        <span className={working ? "c-ldot" : "c-ldot idle"} aria-hidden="true" />
        <b>Signal, right now</b>
        <span className="c-mono c-cbar-note">{working ? "Working" : "Idle"} · {updated}</span>
      </div>
      <div className="c-chips" role="tablist" aria-label="Jobs">
        {jobs.map((entry, n) => (
          <button key={entry.label} type="button" role="tab" aria-selected={n === index} className={n === index ? "c-fchip on" : "c-fchip"} onClick={() => setIndex(n)}>{entry.label}</button>
        ))}
      </div>
      <div role="tabpanel" aria-label={job.label}>
        <p className="c-cpt">{job.title}</p>
        <ul className="c-cs">
          {job.rows.map((row) => (
            <li key={row.label} className={row.value === null ? "" : "done"}><i aria-hidden="true" /><span>{row.label}</span><b>{row.value ?? "Not observed yet"}</b></li>
          ))}
        </ul>
      </div>
      {problems.length > 0 ? (
        <a className="c-attention" href="/settings#health-title"><b>Needs attention</b><span>{problems.join(" · ")}</span></a>
      ) : null}
      <div className="c-meter" aria-hidden="true"><span style={{ width: `${(observed / Math.max(job.rows.length, 1)) * 100}%` }} /></div>
    </section>
  );
}

const CELL: Record<CitationState, string> = { cited: "c", rival: "x", nobody: "n", unknown: "u" };
const CELL_WORDS: Record<CitationState, string> = { cited: "cites you", rival: "cites a rival", nobody: "cites nobody", unknown: "not checked" };

export function CitationsGrid({ title, summary, providers, questions, legend = false, summaryTone = "accent" }: {
  title: string;
  summary: string;
  providers: string[];
  questions: { question: string; cells: CitationState[] }[];
  legend?: boolean;
  summaryTone?: "accent" | "rival";
}) {
  const [focus, setFocus] = useState(0);
  const current = questions[focus];
  const citing = current ? current.cells.filter((cell) => cell === "cited").length : 0;
  return (
    <section className="c-pn c-pn-dark c-in" aria-label={title}>
      <div className="c-cbar"><b>{title}</b><span className={`c-mono c-cbar-note ${summaryTone === "rival" ? "rival" : "accent"}`}>{summary}</span></div>
      <div className="c-field" style={{ gridTemplateColumns: `76px repeat(${Math.max(questions.length, 1)}, minmax(0, 1fr))` }}>
        {providers.map((provider, row) => <span key={provider} className="rl" style={{ gridRow: row + 1, gridColumn: 1 }}>{provider}</span>)}
        {questions.map((question, column) => (
          <span key={question.question} style={{ display: "contents" }}>
            <button type="button" className={`c-colhit${focus === column ? " on" : ""}`} style={{ gridColumn: column + 2, gridRow: `1 / span ${providers.length}` }}
              onMouseEnter={() => setFocus(column)} onFocus={() => setFocus(column)} onClick={() => setFocus(column)}
              aria-label={`${question.question}: ${question.cells.map((cell, row) => `${providers[row]} ${CELL_WORDS[cell]}`).join(", ")}`} />
            {question.cells.map((cell, row) => <span key={row} className="c-cell" style={{ gridColumn: column + 2, gridRow: row + 1 }}><i className={CELL[cell]} /></span>)}
          </span>
        ))}
      </div>
      {legend ? (
        <div className="c-legend"><span><i className="lg-c" />Cites you</span><span><i className="lg-x" />Cites a rival</span><span><i className="lg-n" />Cites nobody</span><span><i className="lg-u" />Not checked</span></div>
      ) : null}
      {current ? <div className="c-qcap"><span>{current.question}</span><span className="c-ml c-ml-d">Cited by {citing} of {providers.length}</span></div> : null}
    </section>
  );
}

const METRIC_LABEL = { clicks: "Clicks", impressions: "Impressions", position: "Position" } as const;
const METRIC_CAPTION = { clicks: "Clicks from search · 90 days", impressions: "Impressions · 90 days", position: "Average position · 90 days (higher on the chart is better)" } as const;

export function MetricChart({ charts, markers }: {
  charts: Record<"clicks" | "impressions" | "position", ChartGeometry | null>;
  markers: ChangeMarker[];
}) {
  const [metric, setMetric] = useState<"clicks" | "impressions" | "position">("clicks");
  return (
    <SearchChart title={METRIC_CAPTION[metric]} geometry={charts[metric]} markers={charts[metric] ? markers : []} tall
      emptyTitle="No daily search data yet" emptyDetail="Connect Search Console and Signal charts it here, with a marker for every change it shipped.">
      <div className="c-seg" role="group" aria-label="Metric">
        {(Object.keys(METRIC_LABEL) as (keyof typeof METRIC_LABEL)[]).map((key) => (
          <button key={key} type="button" className={metric === key ? "on" : undefined} aria-pressed={metric === key} onClick={() => setMetric(key)}>{METRIC_LABEL[key]}</button>
        ))}
      </div>
    </SearchChart>
  );
}

/** The top strip from the prototype: how many decisions wait, one link to the Inbox. Dismissing
    hides it until the count changes; the choice lives only in this browser. */
export function DecisionTicker({ count }: { count: number }) {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    try { if (window.localStorage.getItem("signal.ticker.dismissed") === String(count)) setHidden(true); } catch { /* storage may be unavailable */ }
  }, [count]);
  if (hidden) return null;
  return (
    <div className="c-ticker" role="status">
      <a className="c-ticker-go" href="/approvals"><b>{count}</b>{count === 1 ? "decision is waiting on you" : "decisions are waiting on you"}<span aria-hidden="true">→</span></a>
      <button type="button" className="c-ticker-x" aria-label="Dismiss" onClick={() => {
        try { window.localStorage.setItem("signal.ticker.dismissed", String(count)); } catch { /* storage may be unavailable */ }
        setHidden(true);
      }}>×</button>
    </div>
  );
}
