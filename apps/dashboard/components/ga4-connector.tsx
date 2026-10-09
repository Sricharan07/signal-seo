"use client";

import { Ban, BarChart3, Download, Link, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ga4AuthorizationUrl, validGa4State, type Ga4State } from "../lib/ga4-api";
import { ConnectionStatus } from "./connection-status";

export function Ga4Connector({ siteId, owner }: { siteId: string | null; owner: boolean }) {
  const [state, setState] = useState<Ga4State>({ state: "unavailable" });
  const [stateSite, setStateSite] = useState<string | null>(null);
  const request = useRef(0), activeSite = useRef(siteId);
  activeSite.current = siteId;
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [property, setProperty] = useState(""), [start, setStart] = useState(""), [end, setEnd] = useState("");
  async function load() {
    if (siteId === null || !owner) return;
    const sequence = ++request.current;
    try {
      const response = await fetch(`/auth/ga4?site_id=${encodeURIComponent(siteId)}`, { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(10000) });
      const value: unknown = await response.json();
      if (sequence !== request.current || activeSite.current !== siteId) return;
      setState(response.ok && validGa4State(value) ? value : { state: "unavailable" });
      setStateSite(siteId);
    } catch { if (sequence === request.current) { setState({ state: "unavailable" }); setStateSite(siteId); } }
  }
  useEffect(() => {
    setProperty(""); setNotice(null);
    void load();
    return () => { request.current += 1; };
  }, [siteId, owner]); // eslint-disable-line react-hooks/exhaustive-deps
  async function command(command: Record<string, unknown>) {
    if (busy || siteId === null) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/ga4", { method: "POST", credentials: "same-origin", signal: AbortSignal.timeout(95000),
        headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ site_id: siteId, ...command }) });
      const result = await response.json() as Record<string, unknown>;
      if (activeSite.current !== siteId) return;
      if (!response.ok) { setNotice("GA4 request was not confirmed. Refresh the connector before retrying."); return; }
      if (command.operation === "connect") {
        const url = ga4AuthorizationUrl(result.authorization_url, window.location.origin);
        if (!url) throw new Error();
        window.location.assign(url);
      } else {
        setNotice(result.state === "AUTHORITY_DURABILITY_PENDING" ? "Disconnected locally. Restriction journal confirmation is pending." : null);
        await load();
      }
    } catch { setNotice("GA4 is unavailable. No import or connection was confirmed."); }
    finally { setBusy(false); }
  }
  const available = stateSite === siteId && owner && state.state !== "unavailable";
  const selection = available ? state.selection : null;
  const coverage = available ? state.coverage : null;
  return <section className="slack-connector" aria-labelledby="ga4-title">
    <div className="slack-heading"><h2 id="ga4-title"><BarChart3 size={18} aria-hidden="true" /> Google Analytics 4</h2>
      <ConnectionStatus>{!owner ? "Owner access required" : state.state === "unavailable" ? "Unavailable" : state.state === "read_only" ? "Read only" : selection ? "Choose property" : "Not connected"}</ConnectionStatus></div>
    {!owner ? <p className="muted">An owner with MFA must connect GA4.</p> : !available ? <p className="muted">Google Analytics is not set up for this deployment, or it needs an owner signed in with 2-step verification and a verified site.</p> : state.state === "disconnected" ?
      selection && selection.properties.length ? <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "select", attempt_id: selection.attempt_id, property_resource_name: property }); }}>
        <label>GA4 property<select required value={property} onChange={event => setProperty(event.target.value)}><option value="">Choose property</option>{selection.properties.map(item => <option key={item.resource_name} value={item.resource_name}>{item.display_name || item.resource_name} ({item.resource_name})</option>)}</select></label>
        <button className="button" disabled={busy || !property} type="submit"><Link size={16} aria-hidden="true" /> Confirm property</button>
      </form> : <>{selection ? <p>No accessible GA4 properties were returned.</p> : null}<button className="button button-primary" disabled={busy} onClick={() => void command({ operation: "connect" })}><Link size={16} aria-hidden="true" /> Connect GA4</button></> : state.state === "read_only" ? <>
        <dl className="slack-binding"><div><dt>Property</dt><dd>{state.property_resource_name}</dd></div><div><dt>Last import</dt><dd>{state.imported_at ? new Date(state.imported_at).toLocaleString() : "Not imported"}</dd></div><div><dt>Coverage</dt><dd>{coverage ? "Incomplete" : "Unavailable"}</dd></div></dl>
        <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "import", start_date: start, end_date: end }); }}>
          <label>Start date<input required type="date" max={end || undefined} value={start} onChange={event => setStart(event.target.value)} /></label>
          <label>End date<input required type="date" min={start || undefined} max={start ? new Date(Date.parse(start) + 92 * 86400000).toISOString().slice(0, 10) : undefined} value={end} onChange={event => setEnd(event.target.value)} /></label>
          <button className="button" disabled={busy} type="submit"><Download size={16} aria-hidden="true" /> Import</button>
        </form>
        {coverage ? <><p>Sampling: {coverage.sampling ? "flagged" : "not reported"}. Thresholding: {coverage.thresholding ? "flagged" : "not reported"}. Other rows: {coverage.other_row ? "flagged" : "not reported"}.</p>
          <details><summary>Import coverage</summary><pre style={{ overflowX: "auto", maxWidth: "100%" }}>{JSON.stringify(coverage, null, 2)}</pre></details></> : null}
        <button className="button" disabled={busy} onClick={() => void command({ operation: "disconnect", binding_id: state.binding_id })}><Ban size={16} aria-hidden="true" /> Disconnect GA4</button>
      </> : null}
    {owner && siteId ? <button className="button" disabled={busy} onClick={() => void load()}><RefreshCw size={16} aria-hidden="true" /> Refresh status</button> : null}
    {busy ? <p role="status"><LoaderCircle size={16} aria-hidden="true" /> GA4 request pending</p> : notice ? <p role="status">{notice}</p> : null}
  </section>;
}
