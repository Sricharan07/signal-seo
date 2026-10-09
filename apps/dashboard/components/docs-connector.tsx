"use client";

import { Ban, FileText, Link, LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { NOTION_REASON, type DocsState } from "../lib/docs-api";
import { ConnectionStatus } from "./connection-status";

export function DocsConnector({ state, siteId, owner }: { state: DocsState; siteId: string | null; owner: boolean }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const availability = disconnected ? "revoked" : state.availability;
  const binding = "binding_id" in state ? state : null;
  async function command(operation: string) {
    if (busy || siteId === null) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/google-docs", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" }, signal: AbortSignal.timeout(95000),
        body: JSON.stringify({ site_id: siteId, operation, ...(operation === "disconnect" ? { binding_id: binding?.binding_id } : {}) }) });
      if (response.status !== 200) { setNotice("Google Docs is unavailable. No change was confirmed."); return; }
      const result = await response.json() as Record<string, unknown>;
      if (operation === "connect") {
        if (typeof result.authorization_url !== "string") throw new Error();
        const url = new URL(result.authorization_url);
        if (url.origin !== "https://accounts.google.com" || url.pathname !== "/o/oauth2/v2/auth") throw new Error();
        window.location.assign(url.href);
      } else if (operation === "disconnect" && result.state === "revoked_pending") {
        setDisconnected(true);
        setNotice("Disconnected locally. Recovery-journal confirmation is pending.");
      } else { window.location.reload(); }
    } catch { setNotice("Google Docs is unavailable. Check the latest status before retrying."); }
    finally { setBusy(false); }
  }
  return <>
    <section className="docs-connector" aria-labelledby="docs-title">
      <div className="slack-heading"><h2 id="docs-title"><FileText size={18} aria-hidden="true" /> Google Docs</h2>
        <ConnectionStatus>{availability === "ready" ? "Connected" : availability === "unbound" ? "Not connected" : availability === "degraded" ? "Reconnect required" : availability === "revoked" ? "Disconnected" : "Unavailable"}</ConnectionStatus></div>
      {availability === "unavailable" ? <p className="muted">Google Docs is unavailable for this deployment.</p> : !owner ? <p className="muted">An owner with MFA must manage Google Docs.</p> :
        <div className="docs-actions" aria-busy={busy}>
          {binding === null || availability === "revoked" ? <button className="button button-primary" disabled={busy || siteId === null} onClick={() => void command("connect")}>
            {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Link size={16} aria-hidden="true" />} Connect and pick documents</button> : <>
            {state.availability === "ready" ? <button className="button" disabled={busy} onClick={() => void command("sync")}>{busy ? <LoaderCircle size={16} aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />} Sync documents</button> : <p className="muted">Disconnect, then reconnect to review access again.</p>}
            <button className="button" disabled={busy} onClick={() => void command("disconnect")}><Ban size={16} aria-hidden="true" /> Disconnect Google Docs</button>
          </>}
        </div>}
      {"extraction_availability" in state && state.extraction_availability === "unavailable" ? <p className="muted">Fact extraction unavailable: the model provider is not configured.</p> : null}
      {state.sources.length > 0 ? <ul className="docs-sources">{state.sources.map(source => <li key={source.source_id}>
        <div><span className="docs-file-id">{source.file_id}</span><span>{source.state === "pending" ? "Awaiting first sync" : source.state === "withdrawn" ? "Withdrawn" : `Version ${source.provider_version}`}</span></div>
        {source.observed_at ? <time dateTime={source.observed_at}>{source.state === "withdrawn" ? "Withdrawn" : "Imported"} {new Date(source.observed_at).toLocaleString()}</time> : null}
        {source.document_id ? <><a href="/settings" title={`Document ${source.document_id}`}>Review document text</a><a href="/business-brain">{"extraction_availability" in state && state.extraction_availability === "available" ? "Extract and review proposed facts" : "Review proposed facts"}</a></> : null}
        {source.review_fact_ids.length > 0 ? <><p role="status">{source.review_fact_ids.length} derived {source.review_fact_ids.length === 1 ? "fact needs" : "facts need"} owner review. History is retained.</p><a href="/business-brain">Review withdrawn-source facts</a></> : null}
      </li>)}</ul> : null}
      {notice === null ? null : <p role="status">{notice}</p>}
    </section>
    <section className="docs-connector" aria-labelledby="notion-title"><div className="slack-heading"><h2 id="notion-title">Notion</h2><ConnectionStatus>Unavailable</ConnectionStatus></div><p className="muted">{NOTION_REASON}</p></section>
  </>;
}
