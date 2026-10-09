"use client";
import { readableCode } from "./home-panels";

import { useState } from "react";
import type { ChatChannel, ChatReportState } from "../lib/chat-reports-api";

const LABELS: Record<ChatChannel, string> = { slack_channel: "Slack channel", slack_dm: "Slack direct messages", telegram: "Telegram private chat" };
const DELIVERY: Record<string, string> = { queued: "Queued", retry: "Retry scheduled", dispatching: "Sending; outcome pending",
  accepted: "Provider accepted", unknown: "Outcome unknown; not resent", suppressed: "Not sent", failed: "Retry limit reached" };

export function ChatReportPreferences({ state, siteId }: { state: ChatReportState; siteId: string | null }) {
  const [busy, setBusy] = useState<ChatChannel | null>(null), [notice, setNotice] = useState<string | null>(null);
  async function change(channel: ChatChannel, enabled: boolean) {
    if (busy !== null || siteId === null) return;
    setBusy(channel); setNotice(null);
    try {
      const response = await fetch("/auth/chat-reports", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ site_id: siteId, channel, enabled }), signal: AbortSignal.timeout(12000) });
      const result = await response.json() as { state?: string };
      if (response.status === 200 && result.state === (enabled ? "enabled" : "disabled")) window.location.reload();
      else setNotice("Preference was not confirmed. Check the connector and retry.");
    } catch { setNotice("Preference was not confirmed. Retry when the connection is restored."); }
    finally { setBusy(null); }
  }
  return <section className="settings-section chat-report-settings" aria-labelledby="chat-report-title">
    <h2 id="chat-report-title">Chat reports and alerts</h2>
    {"availability" in state ? <p>Chat report delivery is unavailable for this deployment.</p> : <>
      <div className="chat-report-preferences">{state.channels.map(row => <div className="chat-report-preference" key={row.channel}>
        <label className="email-toggle"><input type="checkbox" checked={row.enabled}
          disabled={busy !== null || siteId === null || !row.enabled && row.availability === "unavailable"}
          onChange={event => void change(row.channel, event.target.checked)} />{LABELS[row.channel]}</label>
        <span>{busy === row.channel ? "Saving" : row.availability === "unavailable" ? "Unavailable" : row.enabled ? "Enabled" : "Off"}</span>
      </div>)}</div>
      <h3>Delivery history</h3>
      {state.history.length === 0 ? <p>No chat report deliveries recorded.</p> :
        <ol className="chat-report-history">{state.history.map(row => <li key={row.id}>
          <div><strong>{LABELS[row.channel]}</strong><span>{row.category === "weekly_report" ? "Weekly report" : readableCode(row.category)}</span></div>
          <div><span>{DELIVERY[row.state]}</span><time dateTime={row.created_at}>{new Date(row.created_at).toISOString().slice(0, 16).replace("T", " ")} UTC</time></div>
          {row.outcome && !["dispatching", "accepted", "unknown"].includes(row.outcome) ? <span className="chat-report-reason">{readableCode(row.outcome)}</span> : null}
        </li>)}</ol>}
    </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}
