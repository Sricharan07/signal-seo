"use client";

import { LoaderCircle, Pause, Play } from "lucide-react";
import { useState } from "react";
import type { WeeklyPauseState } from "../lib/weekly-loop-api";

export function WeeklyLoopControl({ siteId, state }: { siteId: string; state: WeeklyPauseState }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [current, setCurrent] = useState(state);
  async function command() {
    if (busy || current.state !== "available") return;
    const operation = current.paused ? "resume" : "pause";
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/actions/weekly-loop", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ site_id: siteId, operation }), signal: AbortSignal.timeout(18000) });
      const result = await response.json();
      if (response.status !== 200 || result.state !== (operation === "pause" ? "paused" : "pause_cleared") ||
          !["ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"].includes(result.durability)) throw new Error();
      setCurrent({ state: "available", paused: operation === "pause" });
      setNotice(result.durability === "AUTHORITY_DURABILITY_PENDING"
        ? "The change is recorded locally. Recovery protection is still pending; refresh before relying on it."
        : operation === "pause" ? "The loop is paused. Any observation already running may finish." : "The pause is cleared. Revoked allowances stay revoked; grant a new allowance to restart work.");
    } catch { setCurrent({ state: "unavailable" }); setNotice("The change was not confirmed. Refresh to check before retrying."); }
    finally { setBusy(false); }
  }
  return <div className="weekly-control">
    {current.state === "available" ? <>
      <button className="button" disabled={busy} onClick={() => void command()}>
        {busy ? <LoaderCircle size={16} aria-hidden="true" /> : current.paused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}
        {current.paused ? "Resume weekly loop" : "Pause weekly loop"}
      </button>
      <p className="muted">{current.paused ? "Paused. Resuming does not restore revoked allowances." : "Pausing revokes the current allowance. You will need a new one after resuming."}</p>
    </> : <p className="muted">Could not check whether the weekly loop is paused.</p>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </div>;
}
