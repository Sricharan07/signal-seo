"use client";
import { LoaderCircle, Send } from "lucide-react";
import { useState } from "react";

export function TelegramRequest({ siteId, bindingId, revisionId, revisionSha256 }: {
  siteId: string; bindingId: string; revisionId: string; revisionSha256: string;
}) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  async function send() {
    if (busy) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/telegram", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" }, signal: AbortSignal.timeout(25000),
        body: JSON.stringify({ site_id: siteId, operation: "request", binding_id: bindingId, revision_id: revisionId, revision_sha256: revisionSha256 }) });
      const result = await response.json() as Record<string, unknown>;
      setNotice(response.status !== 200 ? "Telegram request unavailable." : result.state === "accepted" ? "Request sent to Telegram." :
        result.state === "queued" ? "Request queued. Retry delivery later." : "Delivery unknown. No duplicate will be sent.");
    } catch { setNotice("Telegram request unavailable."); }
    finally { setBusy(false); }
  }
  return <div><button className="secondary-command" disabled={busy} onClick={() => void send()}>
    {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Send size={16} aria-hidden="true" />} Request in Telegram
  </button>{notice === null ? null : <p role="status">{notice}</p>}</div>;
}
