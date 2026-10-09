"use client";

import { MessageSquare, LoaderCircle } from "lucide-react";
import { useState } from "react";

export function SlackRequest({ siteId, bindingId, channelId, revisionId, revisionSha256 }: {
  siteId: string; bindingId: string; channelId: string; revisionId: string; revisionSha256: string;
}) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  async function send() {
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/slack", {method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/json",Accept:"application/json"},signal:AbortSignal.timeout(18000),
        body:JSON.stringify({site_id:siteId,operation:"request",binding_id:bindingId,channel_id:channelId,
          revision_id:revisionId,revision_sha256:revisionSha256})});
      const result = await response.json() as Record<string, unknown>;
      setNotice(response.status !== 200 ? "Slack request unavailable." : result.state === "accepted" ? "Request sent to Slack." :
        result.state === "queued" ? "Request queued. Retry delivery later." : "Delivery unknown. No duplicate will be sent.");
    } catch { setNotice("Slack request unavailable."); }
    finally { setBusy(false); }
  }
  return <div><button className="secondary-command" disabled={busy} onClick={() => void send()}>
    {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <MessageSquare size={16} aria-hidden="true" />} Request in Slack
  </button>{notice === null ? null : <p role="status">{notice}</p>}</div>;
}
