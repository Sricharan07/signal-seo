import { CircleCheck, CircleHelp, TriangleAlert, OctagonAlert } from "lucide-react";
import type { HealthState } from "../lib/health-api";
import { HEALTH_LABELS, readableCode } from "./home-panels";

const ICONS = { ok: CircleCheck, warning: TriangleAlert, critical: OctagonAlert, unknown: CircleHelp };

export function HealthPanel({ value }: { value: HealthState }) {
  return <section className="settings-section health-panel" aria-labelledby="health-title">
    <h2 id="health-title">Health</h2>
    {value.state === "rejected" ? <p>Health details require the current site owner.</p> :
      value.state === "unavailable" ? <p>Health monitoring is unavailable for this deployment.</p> :
      value.checks.length === 0 ? <p>No health checks have been recorded yet.</p> :
      <ul className="health-checks">{value.checks.map(item => {
        const Icon = ICONS[item.state];
        return <li key={item.check} className="health-check">
          <div><strong>{HEALTH_LABELS[item.check]}</strong><span className={`health-state health-${item.state}`}>
            <Icon size={15} aria-hidden="true" />{item.state === "ok" ? "OK" : item.state[0].toUpperCase() + item.state.slice(1)}</span></div>
          <p>{readableCode(item.reason)}</p>
          <time dateTime={item.checked_at}>{new Date(item.checked_at).toISOString().replace("T", " ").slice(0, 19)} UTC</time>
          {item.state !== "ok" ? <p className="health-remediation">{item.remediation}</p> : null}
        </li>;
      })}</ul>}
  </section>;
}
