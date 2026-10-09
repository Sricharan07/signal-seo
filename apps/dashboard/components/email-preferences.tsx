"use client";
import { readableCode } from "./home-panels";

import { useState } from "react";
import type { EmailState } from "../lib/email-api";

export function EmailPreferences({ state, siteId }: { state: EmailState; siteId: string | null }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  async function change(enabled: boolean) {
    if (busy || siteId === null || state.availability === "unavailable") return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/email", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ site_id: siteId, enabled, address: enabled ? state.address : null }),
        signal: AbortSignal.timeout(12000) });
      const result = await response.json() as { state?: string };
      if (response.status === 200 && result.state === (enabled ? "enabled" : "disabled")) window.location.reload();
      else setNotice("Email preference was not confirmed. Sign in again and retry.");
    } catch { setNotice("Email preference was not confirmed. Retry when the connection is restored."); }
    finally { setBusy(false); }
  }
  return <section className="settings-section" aria-labelledby="email-title">
    <h2 id="email-title">Email notifications</h2>
    {state.availability === "unavailable" ? <p>Email delivery is unavailable for this deployment.</p> : <>
      {state.availability === "identity_unverified" ? <p>Your identity provider has not verified a current email address.</p> :
        <p className="email-address">{state.address}</p>}
      {state.availability === "stale_session" ? <p>Sign in again before enabling email.</p> : null}
      <label className="email-toggle"><input type="checkbox" checked={state.enabled}
        disabled={busy || siteId === null || !state.enabled && !state.can_enable}
        onChange={event => void change(event.target.checked)} /> Weekly reports and alerts</label>
      {state.last_delivery_state === null ? null : <p>Last delivery: {state.last_delivery_state === "accepted" ?
        "Accepted by the mail provider; reading is not confirmed." : state.last_delivery_state === "unknown" ||
        state.last_delivery_state === "dispatching" ? "Outcome unknown; not resent automatically." :
        readableCode(state.last_delivery_state)}</p>}
    </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}
