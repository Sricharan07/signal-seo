"use client";

import { Check, Link, RefreshCw, Unplug } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ConnectionStatus } from "./connection-status";
import { validWebflowData, type WebflowData } from "../lib/webflow-api";
import { validPublishingOptions, webflowAuthorization, type PublishingOptions } from "../lib/webflow-owner-api";

export function WebflowConnection({siteId, initialData, initialOptions}: {siteId: string; initialData?: WebflowData; initialOptions?: PublishingOptions}) {
  const [data, setData] = useState<WebflowData | null>(initialData ?? null);
  const [options, setOptions] = useState<PublishingOptions | null>(initialOptions ?? null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const load = useCallback(async () => {
    const turn = ++generation.current;
    try {
      const r = await fetch(`/actions/webflow?site_id=${siteId}`, {cache: "no-store"});
      const value = await r.json();
      if (!r.ok || !validWebflowData(value)) throw new Error();
      const selection = await fetch(`/actions/webflow/options?site_id=${siteId}`, {cache: "no-store"});
      const choices = await selection.json();
      if (turn !== generation.current) return;
      setData(value);
      setOptions(selection.ok && validPublishingOptions(choices) ? choices : null);
      if (!selection.ok) setNotice("Webflow setup is unavailable. An operator must configure OAuth and shared egress for this verified site.");
    } catch { if (turn === generation.current) { setData(null); setOptions(null); setNotice("Could not check Webflow. Refresh to retry."); } }
  }, [siteId]);
  useEffect(() => { if (!initialData || !initialOptions) void load(); return () => { generation.current++; }; }, [initialData, initialOptions, load]);
  async function act(command: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setNotice("");
    try {
      const r = await fetch("/auth/webflow", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({schema_version: 1, site_id: siteId, ...command})});
      const result = await r.json();
      if (result.state === "step_up_required") { setNotice("Sign in again with MFA, then retry within five minutes."); return; }
      if (!r.ok) throw new Error();
      if (command.operation === "begin") {
        const url = webflowAuthorization(result.authorization_url, window.location.origin);
        if (!url) throw new Error();
        window.location.assign(url); return;
      }
      await load();
      setNotice(result.state === "AUTHORITY_DURABILITY_PENDING" ? "Disconnected locally. The independent revocation record is still pending; refresh to check again." : command.operation === "seal" ? "Draft sealed. Review the exact draft in Inbox." : "Disconnected. The upstream revocation may still be unconfirmed.");
    } catch { setNotice("The outcome is not confirmed. Refresh before retrying."); }
    finally { setBusy(false); }
  }
  const binding = data?.bindings.find(b => !b.revoked_at);
  const articles = options?.articles.filter(a => !data?.inbox.some(i => i.candidate_id === a.candidate_id)) ?? [];
  return <section className="business-brain" aria-label="Webflow connection">
    <header className="brain-toolbar"><h2>Webflow</h2><ConnectionStatus>{!data ? "Could not check" : !options ? "Unavailable" : binding ? "Connected" : "Not connected"}</ConnectionStatus><button title="Refresh Webflow" aria-label="Refresh Webflow" disabled={busy} onClick={() => void load()}><RefreshCw size={16}/></button></header>
    <p>Draft review only. Sending to Webflow is unavailable until live qualification. Publish in Webflow yourself.</p>
    {notice && <p role="status">{notice}</p>}
    {binding ? <>
      <p>Connected to {binding.origin}</p>
      <details className="technical-details"><summary>Technical details</summary><p>Binding {binding.id}</p><p>Webflow site {binding.provider_site}</p><p>Collection {binding.collection_id}</p><p>Title: {binding.field_mapping.title}; description: {binding.field_mapping.description}; body: {binding.field_mapping.body}</p><p>Schema {binding.schema_sha256}</p></details>
      {articles.length ? <form className="brain-form" onSubmit={e => {e.preventDefault(); const article = articles.find(a => a.candidate_id === new FormData(e.currentTarget).get("article")); if (article) void act({operation: "seal", binding_id: binding.id, candidate_id: article.candidate_id, source_sha256: article.source_sha256});}}>
        <label>Article<select name="article" required disabled={busy}>{articles.map(a => <option key={a.candidate_id} value={a.candidate_id}>{a.title}</option>)}</select></label>
        <button className="primary-command" disabled={busy}><Check size={16}/>Seal for review</button>
      </form> : <p>No owner-approved articles are ready. Approve an article for delivery in Articles first.</p>}
      <button disabled={busy} onClick={() => void act({operation: "revoke", binding_id: binding.id})}><Unplug size={16}/>Disconnect</button>
    </> : options && <form className="brain-form" onSubmit={e => {e.preventDefault(); const f = new FormData(e.currentTarget); void act({operation: "begin", provider_site: f.get("provider_site"), collection_id: f.get("collection_id"), field_mapping: {title: "name", description: f.get("description"), body: f.get("body")}});}}>
      <details className="technical-details"><summary>Technical details</summary>
        <label>Webflow site identifier<input name="provider_site" required pattern="[0-9a-f]{24}" maxLength={24}/></label>
        <label>Collection identifier<input name="collection_id" required pattern="[0-9a-f]{24}" maxLength={24}/></label>
        <p>Title maps to name. Description must be editable plain text; body must be editable rich text.</p>
        <label>Description field<input name="description" required pattern="[a-z][a-z0-9-]{0,63}" maxLength={64}/></label>
        <label>Body field<input name="body" required pattern="[a-z][a-z0-9-]{0,63}" maxLength={64}/></label>
      </details>
      <label className="writer-checkbox"><input type="checkbox" required/>I confirm this collection and field mapping.</label>
      <button className="primary-command" disabled={busy}><Link size={16}/>Connect Webflow</button>
    </form>}
    {data?.bindings.filter(b => b.revoked_at).map(b => <details key={b.id} className="technical-details"><summary>Technical details</summary><p>Disconnected binding {b.id}</p><button disabled={busy} onClick={() => void act({operation: "revoke", binding_id: b.id})}><Unplug size={16}/>Check revocation</button></details>)}
  </section>;
}
