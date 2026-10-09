"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, PencilLine, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { brainCategories, brainStatuses, type BrainData, type BrainFact } from "../lib/business-brain-api";
import { readableCode } from "./home-panels";

const label = readableCode;
export function BusinessBrain({ siteId, initialData }: { siteId: string; initialData?: BrainData }) {
  const [data, setData] = useState<BrainData | null>(initialData ?? null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("all");
  const [editing, setEditing] = useState<BrainFact | null>(null);
  const load = useCallback(async () => {
    try {
      const responses = await Promise.all(["facts", "voice", "extraction"].map((resource) => fetch(`/actions/business-brain?site_id=${siteId}&resource=${resource}`, { cache: "no-store" })));
      if (responses.some((item) => !item.ok)) throw new Error();
      const [facts, voice, extraction] = await Promise.all(responses.map((item) => item.json()));
      setData({ facts: facts.facts, voice: voice.voice, extraction });
    } catch { setData(null); setNotice("Business facts are unavailable. Refresh to retry."); }
  }, [siteId]);
  useEffect(() => { if (!initialData) void load(); }, [initialData, load]);

  async function mutate(action: string, values: Record<string, unknown>): Promise<boolean> {
    setBusy(true); setNotice("");
    try {
      const response = await fetch("/actions/business-brain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1, site_id: siteId, action, ...values }) });
      if (!response.ok) throw new Error();
      const result = await response.json();
      if (result.state && result.state !== "completed") setNotice(`Extraction ${label(result.state)}.`);
      await load();
      return true;
    } catch { setNotice("The change was not saved. Refresh before retrying."); return false; }
    finally { setBusy(false); }
  }
  return <div className="business-brain">
    <header className="brain-toolbar"><h2>Facts Signal may use</h2><button type="button" title="Refresh Business facts" aria-label="Refresh Business facts" disabled={busy} onClick={() => void load()}><RefreshCw size={16} /></button></header>
    {notice && <p role="status">{notice}</p>}
    {!data ? <p role="status">{notice ? "No facts loaded." : "Loading Business facts"}</p> : <>
      <p role="status">{data.extraction.state === "unavailable" ? "Extraction unavailable: model provider is not configured." : "Extraction available"}</p>
      <section className="settings-section" aria-labelledby="brain-facts-title">
        <div className="brain-toolbar"><h2 id="brain-facts-title">Your facts</h2><label>Status <select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All</option>{brainStatuses.map((status) => <option key={status} value={status}>{label(status)}</option>)}</select></label></div>
        {data.facts.length === 0 && <p>No business facts yet.</p>}
        {brainStatuses.filter((status) => filter === "all" || filter === status).map((status) => {
          const facts = data.facts.filter((item) => item.status === status);
          if (!facts.length) return null;
          return <section key={status} className="brain-fact-group"><h3>{label(status)}</h3>{brainCategories.map((category) => {
            const group = facts.filter((item) => item.category === category);
            return group.length ? <div key={category}><h4>{label(category)}</h4><ul className="brain-facts">{group.map((item) => <li key={item.fact_id}>
              <div><p>{item.statement}</p><a href={`/actions/business-brain?site_id=${siteId}&resource=provenance&fact_id=${item.fact_id}`} target="_blank" rel="noreferrer" title={item.decision_id ? `Decision ${item.decision_id}` : undefined}>{item.source_kind === "owner_statement" ? "Owner statement" : item.source_kind === "brand_document" ? `Document range ${item.extracted_range?.start}-${item.extracted_range?.end}` : "Crawl page evidence"}</a><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString("en-US", { timeZone: "UTC" })}</time>{item.sensitive && <span>Owner review required</span>}</div>
              {(status === "proposed" || status === "approved") && <div className="brain-actions">
                {item.source_review_required && <span role="status">Source withdrawn: owner review required</span>}
                {status === "proposed" && !item.source_review_required && <button type="button" title="Approve fact" aria-label={`Approve ${item.statement}`} disabled={busy} onClick={() => void mutate("approve", { fact_id: item.fact_id })}><Check size={16} aria-hidden="true" /> Approve</button>}
                <button type="button" title="Correct fact" aria-label={`Correct ${item.statement}`} disabled={busy} onClick={() => setEditing(item)}><PencilLine size={16} aria-hidden="true" /> Correct</button>
                <button type="button" title="Remove fact" aria-label={`Remove ${item.statement}`} disabled={busy} onClick={() => { if (window.confirm("Remove this fact from approved facts? History will remain.")) void mutate("remove", { fact_id: item.fact_id }); }}><Trash2 size={16} aria-hidden="true" /> Remove</button>
              </div>}
            </li>)}</ul></div> : null;
          })}</section>;
        })}
        {editing && <form className="brain-form" onSubmit={(event) => { event.preventDefault(); const statement = String(new FormData(event.currentTarget).get("statement")); void mutate("correct", { fact_id: editing.fact_id, statement }).then((saved) => { if (saved) setEditing(null); }); }}>
          <label>Corrected fact<textarea name="statement" required maxLength={4000} defaultValue={editing.statement} /></label><div className="brain-actions"><button disabled={busy} type="submit"><Check size={16} /> Approve correction</button><button type="button" onClick={() => setEditing(null)}>Cancel</button></div>
        </form>}
        <details><summary>Add an owner statement</summary><form className="brain-form" onSubmit={(event) => { event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); void mutate("propose", { category: values.get("category"), statement: values.get("statement") }).then((saved) => { if (saved) form.reset(); }); }}><label>Category<select name="category">{brainCategories.map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label><label>Statement<textarea name="statement" required maxLength={4000} /></label><button disabled={busy} type="submit"><Plus size={16} /> Add fact</button></form></details>
      </section>
      <section className="settings-section" aria-labelledby="brain-voice-title"><h2 id="brain-voice-title">Brand voice</h2><form key={data.voice?.profile_id ?? "new"} className="brain-form" onSubmit={(event) => { event.preventDefault(); const values = new FormData(event.currentTarget); void mutate("voice", { profile: { tone: values.get("tone"), audience: values.get("audience"), guidelines: values.get("guidelines") }, supersedes_id: data.voice?.profile_id ?? null }); }}>
        {["tone", "audience", "guidelines"].map((field) => <label key={field}>{label(field)}<textarea name={field} maxLength={4000} defaultValue={data.voice?.profile[field as keyof NonNullable<BrainData["voice"]>["profile"]] ?? ""} /></label>)}
        <button disabled={busy} type="submit"><Save size={16} /> Save brand voice</button>
      </form></section>
      {data.extraction.state === "available" && <section className="settings-section"><h2>Extract facts</h2><form className="brain-form" onSubmit={(event) => { event.preventDefault(); const values = new FormData(event.currentTarget); const document = values.get("source_kind") === "brand_document"; void mutate("extract", { source_kind: values.get("source_kind"), source_id: values.get("source_id"), extracted_range: document ? { start: Number(values.get("start")), end: Number(values.get("end")) } : null }); }}><label>Source<select name="source_kind"><option value="page_evidence">Crawl page evidence</option><option value="brand_document">Brand document</option></select></label><label>Evidence or document ID<input name="source_id" required pattern="[0-9a-f-]{36}" /></label><label>Document range start<input name="start" type="number" min={0} defaultValue={0} /></label><label>Document range end<input name="end" type="number" min={1} /></label><button disabled={busy} type="submit"><Plus size={16} /> Extract candidates</button></form></section>}
      {data.extraction.extractions.length > 0 && <section className="settings-section"><h2>Extraction history</h2><ul className="brain-facts">{data.extraction.extractions.map((raw) => { const receipt = raw as { extraction_id: string; state: string; page_type: string | null; provider: string | null; fallback: boolean | null }; return <li key={receipt.extraction_id}><span>{label(receipt.state)}: {receipt.page_type ?? "Not classified"}</span><span>{receipt.provider === "openai_fallback" ? "Labelled frontier fallback" : receipt.provider ?? "Provider unavailable"}</span></li>; })}</ul></section>}
    </>}
  </div>;
}
