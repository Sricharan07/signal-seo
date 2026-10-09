"use client";

import { Ban, Link, LoaderCircle, MessageSquare, Send } from "lucide-react";
import { useState } from "react";
import type { SlackState } from "../lib/slack-api";
import { ConnectionStatus, approvalLimit } from "./connection-status";

export function SlackConnector({ state, siteId, owner }: { state: SlackState; siteId: string | null; owner: boolean }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState(""), [channel, setChannel] = useState(""), [user, setUser] = useState("");
  const [risk, setRisk] = useState("2");
  const binding = state.availability === "bound" ? state : null;

  async function command(body: Record<string, unknown>) {
    if (busy || siteId === null) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/slack", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ site_id: siteId, ...body }), signal: AbortSignal.timeout(18000) });
      const result = await response.json() as Record<string, unknown>;
      if (response.status !== 200) { setNotice("Slack is unavailable. No change was confirmed."); return; }
      if (body.operation === "install" && typeof result.authorization_url === "string") {
        const url = new URL(result.authorization_url);
        if (url.origin !== "https://slack.com" || url.pathname !== "/oauth/v2/authorize") throw new Error();
        window.location.assign(url.href);
      } else if (body.operation === "link") {
        setNotice(result.state === "accepted" ? "Link request sent. Awaiting your confirmation in Slack." :
          result.state === "queued" ? "Link request queued." : "Delivery is unknown. Do not send another request yet.");
      } else { window.location.reload(); }
    } catch { setNotice("Slack is unavailable. No change was confirmed."); }
    finally { setBusy(false); }
  }

  return <section className="slack-connector" aria-labelledby="slack-title">
    <div className="slack-heading"><h2 id="slack-title"><MessageSquare size={18} aria-hidden="true" /> Slack</h2>
      <ConnectionStatus>{state.availability === "bound" ? "Connected" : state.availability === "unbound" ? "Not connected" : "Unavailable"}</ConnectionStatus></div>
    {state.availability === "unavailable" ? <p className="muted">Slack is not configured for this deployment.</p> :
      binding === null ? owner && siteId !== null ? <form onSubmit={event => { event.preventDefault(); void command({ operation: "install", workspace_id: workspace, channel_id: channel, max_risk: Number(risk) }); }} className="slack-form">
        <label>Workspace ID<input required pattern="T[A-Z0-9]{7,63}" maxLength={64} value={workspace} onChange={event => setWorkspace(event.target.value)} autoComplete="off" /></label>
        <label>Channel ID<input required pattern="[CG][A-Z0-9]{7,63}" maxLength={64} value={channel} onChange={event => setChannel(event.target.value)} autoComplete="off" /></label>
        <label>Can approve up to<select value={risk} onChange={event => setRisk(event.target.value)}>{[0, 1, 2].map(level => <option key={level} value={level}>{approvalLimit(level)}</option>)}</select></label>
        <button className="button button-primary" disabled={busy} type="submit">{busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Link size={16} aria-hidden="true" />} Connect Slack</button>
      </form> : <p className="muted">An owner must connect Slack.</p> : <>
        <dl className="slack-binding"><div><dt>Workspace</dt><dd><code>{binding.workspace_id}</code></dd></div><div><dt>Channel</dt><dd><code>{binding.channel_id}</code></dd></div><div><dt>Can approve up to</dt><dd>{approvalLimit(binding.max_risk)}</dd></div></dl>
        {binding.link_id === null ? <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "link", binding_id: binding.binding_id, slack_user_id: user }); }}>
          <label>Your Slack user ID<input required pattern="[UW][A-Z0-9]{7,63}" maxLength={64} value={user} onChange={event => setUser(event.target.value)} autoComplete="off" /></label>
          <button className="button" disabled={busy} type="submit"><Send size={16} aria-hidden="true" /> Send link request</button>
        </form> : <div className="slack-actions"><span>Linked to your Slack account <code>{binding.slack_user_id}</code></span><button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id, link_id: binding.link_id })}><Ban size={16} aria-hidden="true" /> Unlink account</button></div>}
        {owner ? <button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id })}><Ban size={16} aria-hidden="true" /> Disconnect Slack</button> : null}
      </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}
