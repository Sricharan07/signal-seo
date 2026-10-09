"use client";

import { useState } from "react";
import { Check, ListPlus, Plus, X } from "lucide-react";
import { validQuestionProposal, type QuestionProposal } from "../lib/ai-visibility-api";

export function AiQuestionSet({ siteId, onApproved }: { siteId: string; onApproved: () => Promise<void> }) {
  const [proposal, setProposal] = useState<QuestionProposal | null>(null);
  const [questions, setQuestions] = useState<string[]>([]);
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function submit(approve: boolean) {
    setBusy(true); setNotice("");
    try {
      const response = await fetch("/actions/ai-visibility", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1, site_id: siteId, action: approve ? "questions-approve" : "questions-propose", ...(approve && proposal ? { request_id: requestId, crawl_manifest_id: proposal.crawl_manifest_id, supersedes_id: proposal.supersedes_id, questions } : {}) }) });
      const value: unknown = await response.json();
      if (!response.ok) throw new Error();
      if (approve) {
        if (!value || typeof value !== "object" || !("state" in value) || value.state !== "approved") throw new Error();
        setProposal(null); await onApproved(); setNotice("Question version approved. Observations will use this set.");
      } else if (validQuestionProposal(value)) {
        setProposal(value); setQuestions(value.questions.map(q => q.question)); setRequestId(crypto.randomUUID());
      } else { setProposal(null); setNotice("A committed crawl with usable page titles or headings is required."); }
    } catch { setNotice("The request could not be confirmed. Sign in with MFA, then refresh the proposal before retrying."); }
    finally { setBusy(false); }
  }
  const ownerCount = questions.filter(q => !proposal?.questions.some(p => p.question === q.trim().replace(/\s+/g, " "))).length;
  const valid = questions.length > 0 && ownerCount <= 10 && questions.every(q => q.trim().length >= 8 && q.length <= 512) && new Set(questions.map(q => q.trim().replace(/\s+/g, " ").toLowerCase())).size === questions.length;
  return <section className="settings-section" aria-labelledby="question-set-title">
    <h2 id="question-set-title">Target questions</h2>
    {notice && <p role="status">{notice}</p>}
    {!proposal ? <button className="primary-command" disabled={busy} onClick={() => void submit(false)}><ListPlus size={16} />Propose questions from latest crawl</button> : <form className="brain-form" onSubmit={event => { event.preventDefault(); void submit(true); }}>
      {questions.map((question, index) => <div key={index}><label>Question {index + 1}<textarea required minLength={8} maxLength={512} value={question} disabled={busy} onChange={event => { setQuestions(questions.map((q, i) => i === index ? event.target.value : q)); setRequestId(crypto.randomUUID()); }} /></label><button type="button" title={`Remove question ${index + 1}`} aria-label={`Remove question ${index + 1}`} disabled={busy} onClick={() => { setQuestions(questions.filter((_, i) => i !== index)); setRequestId(crypto.randomUUID()); }}><X size={16} /></button></div>)}
      <p>{questions.length} of 25 questions · {ownerCount} of 10 added or edited</p>
      <div className="brain-actions"><button type="button" disabled={busy || questions.length >= 25 || ownerCount >= 10} onClick={() => { setQuestions([...questions, ""]); setRequestId(crypto.randomUUID()); }}><Plus size={16} />Add question</button><button className="primary-command" type="submit" disabled={busy || !valid}><Check size={16} />Approve question version</button><button type="button" disabled={busy} onClick={() => setProposal(null)}>Cancel</button></div>
      <details className="technical-details"><summary>Technical details</summary><p>Crawl manifest {proposal.crawl_manifest_id}</p><p>Previous question version {proposal.supersedes_id ?? "None"}</p></details>
    </form>}
  </section>;
}
