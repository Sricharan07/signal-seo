"use client";
import { useState } from "react";
import { KeyRound, Plus } from "lucide-react";
import type { IndexNowState } from "../lib/indexnow-api";
import { ConnectionStatus } from "./connection-status";
import { readableCode } from "./home-panels";

const LABELS = { not_created: "Not created", pr_open: "Pull request open", deployed: "Deployed", mismatch: "Mismatch" };
const STATUS = { not_created: "Not created", pr_open: "Key file waiting to merge", deployed: "Available", mismatch: "Key file mismatch" };
export function IndexNowConnector({ state, siteId = null, owner = false }: { state: IndexNowState; siteId?: string | null; owner?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [requestId] = useState(() => globalThis.crypto.randomUUID());
  async function create() {
    if (!siteId || busy || state.state !== "available" || !state.canCreate) return;
    setBusy(true);
    try {
      const response = await fetch("/actions/indexnow", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ site_id: siteId, request_id: requestId }) });
      const value = await response.json();
      if (!response.ok || value.state !== "sealed") {
        setNotice(value.state === "step_up_required" ? "Sign in again with MFA before creating the key." : "Key creation was not confirmed. Refresh status before retrying.");
      } else { setNotice("Key file is ready for review in the Inbox."); }
    } catch { setNotice("Key creation was not confirmed. Refresh status before retrying."); }
    finally { setBusy(false); }
  }
  return <section className="indexnow-connector" aria-labelledby="indexnow-heading">
    <div className="slack-heading"><h2 id="indexnow-heading"><KeyRound size={16} aria-hidden="true" /> IndexNow</h2>
      <ConnectionStatus>{state.state === "available" ? STATUS[state.keyStatus] : state.state === "rejected" ? "Owner access required" : "Unavailable"}</ConnectionStatus></div>
    {state.state !== "available" ? <p>IndexNow {state.state === "rejected" ? "requires current owner access." : "is unavailable."}</p> : <>
      <dl className="indexnow-binding"><div><dt>Key file</dt><dd>{LABELS[state.keyStatus]}</dd></div><div><dt>Latest reason</dt><dd>{readableCode(state.reason)}</dd></div></dl>
      {owner && siteId && state.keyStatus === "not_created" && <button className="button" disabled={busy || !state.canCreate} title={!state.canCreate ? "Key creation is unavailable in this configuration" : undefined} onClick={() => void create()}><Plus size={16} aria-hidden="true" />{busy ? "Preparing key file" : "Create IndexNow key"}</button>}
      {notice && <p role="status">{notice}{notice === "Key file is ready for review in the Inbox." && <> <a href="/approvals">Review key file</a></>}</p>}
      {state.submissions.length === 0 ? <p>No changed-URL submissions recorded.</p> : <ul className="indexnow-receipts">
        {state.submissions.map((s, i) => <li key={`${s.changeId}-${s.recordedAt}-${i}`}>
          <strong>{readableCode(s.state)}{s.providerStatus === null ? "" : ` (HTTP ${s.providerStatus})`}</strong>
          {s.urls.map(url => <p key={url}>{url}</p>)}
          <p>{readableCode(s.reason)}</p><time dateTime={s.recordedAt}>{s.recordedAt}</time>
        </li>)}
      </ul>}
    </>}
  </section>;
}
