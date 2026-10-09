import type { ActivityEntry, ActivityKind } from "../lib/insights";

/* The Activity log: what Signal did, and what allowed it to, grouped by day. */

const KIND_LABEL: Record<ActivityKind, string> = { shipped: "Shipped", drafted: "Drafted", decided: "Decided", measured: "Measured", held: "Held back" };
const KIND_PILL: Record<ActivityKind, string> = { shipped: "c-pill o", drafted: "c-pill o", decided: "c-pill o", measured: "c-pill g", held: "c-pill w" };
const TONE_PILL = { you: "c-pill l", good: "c-pill g", warn: "c-pill w", busy: "c-pill k", plain: "c-pill" } as const;
export const ACTIVITY_FILTERS: { key: "all" | ActivityKind; label: string }[] = [
  { key: "all", label: "All" }, { key: "shipped", label: "Shipped" }, { key: "drafted", label: "Drafted" },
  { key: "decided", label: "Decided" }, { key: "measured", label: "Measured" }, { key: "held", label: "Held back" },
];

function dayHeading(iso: string, today: string): string {
  const date = new Date(iso);
  const label = new Intl.DateTimeFormat("en", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" }).format(date);
  return iso.slice(0, 10) === today.slice(0, 10) ? `Today · ${label}` : label;
}

export function ActivityHeader({ filter }: { filter: "all" | ActivityKind }) {
  return (
    <header className="c-ph">
      <div>
        <span className="c-ml">Activity · every change logged</span>
        <h1 className="c-h1"><span className="c-ln"><span>What Signal did,</span></span><span className="c-ln"><span className="c-soft" style={{ animationDelay: ".1s" }}>and what allowed it to.</span></span></h1>
      </div>
      <nav className="c-flt" aria-label="Filter">
        {ACTIVITY_FILTERS.map(({ key, label }) => (
          <a key={key} href={key === "all" ? "/changes" : `/changes?kind=${key}`} className={filter === key ? "on" : undefined} aria-current={filter === key ? "true" : undefined}>{label}</a>
        ))}
      </nav>
    </header>
  );
}

export function ActivityLog({ entries, filter, today }: { entries: ActivityEntry[]; filter: "all" | ActivityKind; today: string }) {
  const shown = entries.filter((entry) => filter === "all" || entry.kind === filter);
  const days = new Map<string, ActivityEntry[]>();
  for (const entry of shown) {
    const key = entry.at.slice(0, 10);
    days.set(key, [...(days.get(key) ?? []), entry]);
  }
  let delay = 0;
  return (
    <section className="c-pn c-in c-activity" aria-label="Activity log">
      {shown.length === 0 ? (
        <p className="c-tb-empty c-activity-empty">{filter === "all" ? "Nothing recorded yet. Every change Signal prepares, ships, measures or holds back appears here." : "Nothing of this kind recorded yet."}</p>
      ) : [...days].map(([day, items]) => (
        <div key={day}>
          <div className="c-lgday"><span className="c-ml">{dayHeading(`${day}T00:00:00Z`, today)}</span></div>
          <div className="c-log">
            {items.map((entry, index) => {
              const body = <>
                <span className="c-mono c-lg-time">{entry.exactTime ? new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }).format(new Date(entry.at)) : "·"}</span>
                <span className="k"><span className={KIND_PILL[entry.kind]}>{KIND_LABEL[entry.kind]}</span></span>
                <div><p>{entry.text}</p><span className="who">{entry.authority}</span></div>
                <span className="r"><span className={TONE_PILL[entry.tone]}>{entry.status}</span></span>
              </>;
              const style = { animationDelay: `${(delay++) * 0.04}s` };
              return entry.href ? (
                <a key={`${entry.at}:${index}`} className="c-lg c-rs" href={entry.href} style={style}>{body}</a>
              ) : (
                <div key={`${entry.at}:${index}`} className="c-lg c-rs" style={style}>{body}</div>
              );
            })}
          </div>
        </div>
      ))}
      <p className="c-activity-foot">Times are UTC. Entries without a time are weekly-loop outcomes recorded for that week.</p>
    </section>
  );
}
