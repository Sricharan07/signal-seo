"use client";

import { Ban, KeyRound, LoaderCircle, Save } from "lucide-react";
import { useState } from "react";
import { parseDataForSeo, type DataForSeoState } from "../lib/dataforseo-api";
import { ConnectionStatus } from "./connection-status";

const usd = (micros: number) => new Intl.NumberFormat("en", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(micros / 1000000);

export function DataForSeoConnector({ state: initial, siteId, owner }: { state: DataForSeoState; siteId: string | null; owner: boolean }) {
  const [state, setState] = useState(initial), [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [login, setLogin] = useState(""), [password, setPassword] = useState("");
  const [cap, setCap] = useState(initial.availability === "unavailable" ? "5.00" : (initial.cap_micros / 1000000).toFixed(2));
  const labels = { available: "Available", unavailable: "Unavailable", unconfigured: "Not configured", cap_exhausted: "Monthly cap exhausted", secret_unavailable: "Credential unavailable", execution_unavailable: "Execution unavailable" };
  const canEdit = owner && siteId !== null && state.availability !== "unavailable";
  async function command(value: Record<string, unknown>) {
    if (busy || !canEdit) return;
    setBusy(true); setNotice(null);
    const body = JSON.stringify({ site_id: siteId, ...value });
    setLogin(""); setPassword("");
    try {
      const response = await fetch("/actions/dataforseo", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" }, body, signal: AbortSignal.timeout(18000) });
      const next = parseDataForSeo(await response.json());
      if (response.status !== 200 || next.availability === "unavailable") throw new Error();
      setState(next); setNotice("Settings updated.");
    } catch { setNotice("No change was confirmed. Refresh to check the current settings before retrying."); }
    finally { setBusy(false); }
  }
  return <section className="slack-connector" aria-labelledby="dataforseo-title">
    <div className="slack-heading"><h2 id="dataforseo-title">DataForSEO</h2><ConnectionStatus>{labels[state.availability]}</ConnectionStatus></div>
    <p className="muted">{state.availability === "unavailable" ? "DataForSEO setup is unavailable for this deployment." : state.availability === "available" ? "Competitor gaps, search volumes, and competitor backlinks are available." : "Competitor gaps, search volumes, and competitor backlinks are unavailable. Search Console history remains independent."}</p>
    {state.availability !== "unavailable" ? <dl className="slack-binding"><div><dt>Month (UTC)</dt><dd>{state.month}</dd></div><div><dt>Spent or reserved</dt><dd>{usd(state.usage_micros)}</dd></div><div><dt>Monthly cap</dt><dd>{usd(state.cap_micros)}</dd></div></dl> : null}
    {canEdit ? <>
      <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "credential", login, password }); }}>
        <label>API login<input required type="password" autoComplete="off" maxLength={254} value={login} onChange={event => setLogin(event.target.value)} disabled={busy} /></label>
        <label>API password<input required type="password" autoComplete="new-password" minLength={8} maxLength={512} value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /></label>
        <button className="button" disabled={busy} type="submit">{busy ? <LoaderCircle size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />} Store credential</button>
      </form>
      <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "cap", cap_micros: Math.round(Number(cap) * 1000000) }); }}>
        <label>Monthly cap (USD)<input required type="number" min="0" max="1000" step="0.01" value={cap} onChange={event => setCap(event.target.value)} disabled={busy} /></label>
        <button className="button" disabled={busy} type="submit"><Save size={16} aria-hidden="true" /> Save cap</button>
      </form>
      {state.credential_configured ? <button className="button" disabled={busy} onClick={() => void command({ operation: "remove" })}><Ban size={16} aria-hidden="true" /> Remove credential</button> : null}
    </> : null}
    {notice ? <p role="status">{notice}</p> : null}
  </section>;
}
