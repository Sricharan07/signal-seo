"use client";

import { useCallback, useEffect, useState } from "react";
import { Eye, FilePlus2, Trash2 } from "lucide-react";

interface DocumentItem {
  document_id: string;
  display_name: string;
  media_type: string;
  created_at: string;
  supersedes_id: string | null;
  injection_signal: boolean;
  secret_signal: boolean;
  deleted: boolean;
  retained_for_evidence: boolean;
}

export function BrandDocuments({ siteId }: { siteId: string }) {
  const [items, setItems] = useState<DocumentItem[]>([]);
  const [state, setState] = useState("Loading documents");
  const [file, setFile] = useState<File | null>(null);
  const [supersedes, setSupersedes] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ id: string; text: string } | null>(null);
  const load = useCallback(async () => {
    try {
      const response = await fetch(`/actions/brand-documents?site_id=${siteId}`, { cache: "no-store" });
      if (!response.ok) throw new Error();
      const value = await response.json() as { documents: DocumentItem[] };
      setItems(value.documents);
      setState("");
    } catch {
      setState("Document storage is unavailable.");
    }
  }, [siteId]);
  useEffect(() => { void load(); }, [load]);

  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || file.size > 2 * 1024 * 1024 || file.size === 0) {
      setState("Choose a file under 2 MB.");
      return;
    }
    setBusy(true);
    try {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
        reader.onerror = () => reject(new Error());
        reader.readAsDataURL(file);
      });
      const response = await fetch("/actions/brand-documents", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ schema_version: 1, site_id: siteId,
          filename: file.name, content_base64: encoded,
          supersedes_id: supersedes || null }),
      });
      if (!response.ok) {
        const value = await response.json() as { error?: { message?: string } };
        setState(value.error?.message ?? "The document could not be uploaded.");
      } else {
        setFile(null);
        setSupersedes("");
        await load();
      }
    } catch {
      setState("The document could not be uploaded.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(documentId: string) {
    if (!window.confirm("Remove this document from future use? Retained evidence is not erased.")) return;
    setBusy(true);
    try {
      const response = await fetch("/actions/brand-documents/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ schema_version: 1, site_id: siteId, document_id: documentId }),
      });
      if (!response.ok) throw new Error();
      await load();
    } catch {
      setState("The document could not be removed.");
    } finally {
      setBusy(false);
    }
  }

  async function view(documentId: string) {
    try {
      const response = await fetch(
        `/actions/brand-documents/${documentId}?site_id=${siteId}`, { cache: "no-store" },
      );
      if (!response.ok) throw new Error();
      const value = await response.json() as { text: string };
      setPreview({ id: documentId, text: value.text });
    } catch {
      setState("This document cannot be viewed. Credential-bearing content is withheld.");
    }
  }

  const active = items.filter((item) => !item.deleted && !items.some((next) => next.supersedes_id === item.document_id));
  return (
    <section className="settings-section" aria-labelledby="brand-documents-title">
      <h2 id="brand-documents-title">Brand documents</h2>
      <p className="section-detail">Product sheets, pricing pages or style guides. Signal only proposes facts from them; you approve each one in Business facts.</p>
      <form className="brand-document-form" onSubmit={(event) => void upload(event)}>
        <input type="file" accept=".pdf,.docx,.md,.txt" aria-label="Brand document"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        <select aria-label="Supersede document" value={supersedes}
          onChange={(event) => setSupersedes(event.target.value)}>
          <option value="">New document</option>
          {active.map((item) => <option key={item.document_id} value={item.document_id}>{item.display_name}</option>)}
        </select>
        <button type="submit" disabled={busy || !file}><FilePlus2 size={16} /> Upload</button>
      </form>
      {state && <p role="status">{state}</p>}
      <ul className="brand-document-list">
        {items.map((item) => <li key={item.document_id}>
          <span>{item.display_name}</span>
          <span>{item.deleted ? "Deleted, evidence retained" :
            items.some((next) => next.supersedes_id === item.document_id) ? "Superseded" :
            item.secret_signal ? "Credential detected; content withheld" :
            item.injection_signal ? "Instruction-like text detected" : "Current"}</span>
          <span className="brand-document-actions">
            {!item.deleted && !item.secret_signal && !items.some((next) => next.supersedes_id === item.document_id) &&
              <button type="button" title="View document" aria-label={`View ${item.display_name}`}
                onClick={() => void view(item.document_id)}><Eye size={16} /></button>}
            {!item.deleted && <button type="button" title="Delete document" aria-label={`Delete ${item.display_name}`}
              disabled={busy} onClick={() => void remove(item.document_id)}><Trash2 size={16} /></button>}
          </span>
        </li>)}
      </ul>
      {preview && <div className="brand-document-preview">
        <button type="button" onClick={() => setPreview(null)}>Close</button>
        <pre aria-label="Document text">{preview.text}</pre>
      </div>}
    </section>
  );
}
