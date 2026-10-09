"use client";
import { readableCode } from "./home-panels";

import { useState } from "react";

import type { BrainFact } from "../lib/business-brain-api";
import type { WriterData } from "../lib/content-writer-api";
import { decodeEntities } from "../lib/serp";

/* Article and fact decisions in the Inbox. Each button sends the same command
   the Articles and Business facts pages send; nothing here grants authority. */

type Candidate = WriterData["candidates"][number];
type Draft = WriterData["drafts"][number];
type Sentence = { path: string; sentence: string; reasons: string[] };

const text = (html: string) => decodeEntities(html.replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();

/** Title, opening paragraph and section outline of the sealed article, as plain text. */
export function articleOutline(html: string): { title: string | null; intro: string | null; sections: string[]; words: number } {
  const title = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ?? html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1];
  const intro = html.match(/<p[^>]*>([\s\S]*?)<\/p>/i)?.[1];
  const sections = [...html.matchAll(/<h2[^>]*>([\s\S]*?)<\/h2>/gi)].map((match) => text(match[1])).filter(Boolean).slice(0, 12);
  const body = text(html);
  return { title: title ? text(title) : null, intro: intro ? text(intro) : null, sections, words: body ? body.split(" ").length : 0 };
}

async function post(path: string, body: Record<string, unknown>): Promise<boolean> {
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return response.ok;
  } catch {
    return false;
  }
}

export function ArticleReview({ siteId, candidate, draft, topic }: { siteId: string; candidate: Candidate; draft: Draft | null; topic: string }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const file = candidate.manifest.changed_files[0];
  const outline = articleOutline(file?.after ?? "");
  const grounding = candidate.manifest.grounding as { sentences?: Sentence[] } | null;
  const sentences = Array.isArray(grounding?.sentences) ? grounding.sentences : [];
  const flagged = sentences.filter((sentence) => sentence.reasons.length > 0);
  const quality = draft?.result.quality;
  const originality = draft?.result.originality as { state: string; eight_gram_overlap: number } | undefined;
  const minutes = Math.max(1, Math.round(outline.words / 220));
  const pending = candidate.review_status === "pending";
  const approved = candidate.review_status === "approved" && !candidate.delivery_approval_id;

  async function act(action: "review" | "approve-delivery", values: Record<string, unknown>, success: string) {
    setBusy(true);
    setNotice(null);
    const ok = await post("/actions/content-writer", { schema_version: 1, site_id: siteId, action, candidate_id: candidate.candidate_id, revision_sha256: candidate.revision_sha256, ...values });
    setBusy(false);
    if (ok) { setNotice(success); window.location.reload(); } else setNotice("That did not go through. Nothing changed. Refresh and try again.");
  }

  return (
    <>
      <div className="c-det-b">
        <div>
          <div className="c-tags">
            <span className="c-pill o">Article · new page</span>
            {flagged.length > 0 ? <span className="c-pill w">{flagged.length} {flagged.length === 1 ? "question" : "questions"} for you</span> : null}
            <span className="c-pill o">{minutes} min read</span>
          </div>
          <h2>{topic}</h2>
          <p className="c-lede">Written from your approved facts and brand voice. Nothing is published until you approve it and merge the pull request.</p>
        </div>
        <article className="c-doc">
          <span className="c-ml">Article · {file?.path ?? "new page"}</span>
          <h3>{outline.title ?? topic}</h3>
          {outline.intro ? <p>{outline.intro}</p> : <p className="c-soft">The draft has no opening paragraph yet.</p>}
          {outline.sections.length > 0 ? <ol>{outline.sections.map((section) => <li key={section}>{section}</li>)}</ol> : null}
        </article>
        <div className="c-meters">
          <div className="c-meter2">Writing<b>{quality ? (quality.state === "passed" ? "Reads naturally" : "Needs work") : "Not checked"}</b>{quality && quality.reasons.length > 0 ? quality.reasons.map((reason) => readableCode(reason).toLowerCase()).join(", ") : quality ? "No filler phrases found" : "No quality result recorded"}</div>
          <div className="c-meter2">Originality<b>{originality ? `${Math.round((1 - originality.eight_gram_overlap) * 100)}% original` : "Not checked"}</b>{originality ? "Against your pages and its sources" : "No originality result recorded"}</div>
          <div className="c-meter2">Facts<b>{sentences.length === 0 ? "No claims recorded" : flagged.length === 0 ? "All claims grounded" : `${sentences.length - flagged.length} of ${sentences.length} grounded`}</b>{flagged.length === 0 ? "Every claim matches a fact you approved" : "Claims below need your answer"}</div>
        </div>
        {approved ? flagged.map((sentence) => (
          <div key={sentence.path} className="c-q-card">
            <span className="c-ml" style={{ color: "var(--ink)" }}>A claim needs you</span>
            <p className="c-quote">“{sentence.sentence}”</p>
            <span className="c-q-why">It isn’t in your approved facts yet ({sentence.reasons.map((reason) => readableCode(reason).toLowerCase()).join(", ")}). Is it true?</span>
            <label className="c-check">
              <input type="checkbox" checked={acknowledged.includes(sentence.path)} disabled={busy}
                onChange={(event) => setAcknowledged((current) => event.target.checked ? [...current, sentence.path] : current.filter((path) => path !== sentence.path))} />
              Yes, it’s true. Keep it in the article.
            </label>
          </div>
        )) : null}
        {notice ? <p role="status" className="c-okrow">{notice}</p> : null}
        <details className="technical-details">
          <summary>Technical details</summary>
          <dl className="change-scope">
            <div><dt>Candidate</dt><dd><code>{candidate.candidate_id}</code></dd></div>
            <div><dt>Revision SHA-256</dt><dd><code>{candidate.revision_sha256}</code></dd></div>
            <div><dt>Approval class</dt><dd>{candidate.manifest.approval_class}</dd></div>
          </dl>
        </details>
      </div>
      <div className="decision-bar" aria-label="Article decisions">
        {pending ? (
          <>
            <span className="decision-hint">Approving the article is the first step. You then approve the pull request separately.</span>
            <div className="decision-actions">
              <button type="button" className="danger-command" disabled={busy} onClick={() => void act("review", { decision: "rejected" }, "Rejected.")}>Reject</button>
              <button type="button" className="secondary-command" disabled={busy} onClick={() => void act("review", { decision: "changes_requested" }, "Sent back for changes.")}>Request changes</button>
              <button type="button" className="primary-command" disabled={busy} onClick={() => void act("review", { decision: "approved" }, "Article approved.")}>Approve article</button>
            </div>
          </>
        ) : approved ? (
          <>
            <span className="decision-hint">{flagged.some((sentence) => !acknowledged.includes(sentence.path)) ? "Answer the questions above to continue." : "Approving opens a pull request that adds this page. Needs 2-step verification in the last 5 minutes."}</span>
            <div className="decision-actions">
              <button type="button" className="primary-command" disabled={busy || flagged.some((sentence) => !acknowledged.includes(sentence.path))}
                onClick={() => void act("approve-delivery", { acknowledged_sentences: acknowledged }, "Approved. Signal will open the pull request.")}>Approve and open a pull request</button>
            </div>
          </>
        ) : (
          <span className="decision-hint">{candidate.delivery_approval_id ? "Pull request approved. Signal opens it next." : `Decision recorded: ${readableCode(candidate.review_status)}.`}</span>
        )}
      </div>
    </>
  );
}

const SOURCE: Record<string, string> = { owner_statement: "Something you told Signal", brand_document: "One of your brand documents", page_evidence: "A page on your site" };

export function FactReview({ siteId, fact }: { siteId: string; fact: BrainFact }) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [statement, setStatement] = useState(fact.statement);
  const [notice, setNotice] = useState<string | null>(null);

  async function act(action: "approve" | "correct" | "remove", values: Record<string, unknown>, success: string) {
    setBusy(true);
    setNotice(null);
    const ok = await post("/actions/business-brain", { schema_version: 1, site_id: siteId, action, fact_id: fact.fact_id, ...values });
    setBusy(false);
    if (ok) { setNotice(success); window.location.reload(); } else setNotice("That did not go through. Nothing changed. Refresh and try again.");
  }

  const found = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(fact.created_at));
  return (
    <>
      <div className="c-det-b">
        <div>
          <div className="c-tags"><span className="c-pill o">Fact to confirm</span><span className="c-pill o">Found {found}</span>{fact.sensitive ? <span className="c-pill w">Sensitive claim</span> : null}</div>
          <h2>Can Signal say “{fact.statement}”?</h2>
          <p className="c-lede">Signal only writes facts you’ve confirmed, so it hasn’t used this one anywhere yet.</p>
        </div>
        <div>
          <span className="c-ml c-lbl">Where it was found</span>
          <p className="c-source">{SOURCE[fact.source_kind] ?? "A recorded source"}{fact.extracted_range ? `, characters ${fact.extracted_range.start}–${fact.extracted_range.end}` : ""}, read {found}.</p>
          <a className="c-source-link" href={`/actions/business-brain?site_id=${siteId}&resource=provenance&fact_id=${fact.fact_id}`} target="_blank" rel="noreferrer">Open the source record</a>
        </div>
        <div>
          <span className="c-ml c-lbl">Where Signal would use it</span>
          <p className="c-lede" style={{ margin: 0, fontSize: 15 }}>Article introductions and page descriptions where it helps. Pricing, legal and customer-named pages always come to you first.</p>
        </div>
        {editing ? (
          <label className="c-edit"><span className="c-ml">Edit the fact</span><textarea className="c-inp" value={statement} maxLength={4000} onChange={(event) => setStatement(event.target.value)} /></label>
        ) : null}
        {notice ? <p role="status" className="c-okrow">{notice}</p> : null}
      </div>
      <div className="decision-bar" aria-label="Fact decisions">
        <span className="decision-hint">Signal will only use it where proof helps.</span>
        <div className="decision-actions">
          <button type="button" className="danger-command" disabled={busy} onClick={() => void act("remove", {}, "Removed. Signal will not use it.")}>No, don’t use it</button>
          {editing ? (
            <button type="button" className="secondary-command" disabled={busy || statement.trim() === ""} onClick={() => void act("correct", { statement: statement.trim() }, "Corrected and approved.")}>Save the correction</button>
          ) : (
            <button type="button" className="secondary-command" disabled={busy} onClick={() => setEditing(true)}>Edit the fact</button>
          )}
          <button type="button" className="primary-command" disabled={busy || editing} onClick={() => void act("approve", {}, "Saved to your business facts.")}>Yes, it’s accurate</button>
        </div>
      </div>
    </>
  );
}
