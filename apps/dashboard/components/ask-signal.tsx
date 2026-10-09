"use client";

import { Search, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  MAX_MEMORY, MAX_QUESTION,
  type AssistantConversation, type AssistantMemory, type AssistantMessage, type AssistantOverview,
} from "../lib/assistant-api";

/* Ask Signal. Questions go to the API, which answers only from the site's own
   records and keeps the conversation and its memories. Nothing here executes a
   change: actions are links into the existing owner flows, which keep their checks. */

const STATE_NOTE: Record<AssistantMessage["state"], string | null> = {
  answered: null, no_record: "No record to answer from", unavailable: "Signal could not answer", failed: "Answer failed", outcome_unknown: "Outcome unknown",
};
const UNAVAILABLE_REASON: Record<AssistantOverview["availability"], string> = {
  available: "",
  model_unconfigured: "Ask Signal needs a language model, and none is set up for this site yet.",
  budget_exhausted: "This month’s question budget is used up. Ask Signal returns when it resets.",
  unavailable: "Ask Signal can’t be reached right now. Your conversations and memories are kept.",
};
const FAILED = "That did not go through. Send it again: Signal won’t answer or charge twice.";

async function readJson(url: string): Promise<unknown> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch { return null; }
}

/** Posts one owner command. Returns the body on success, or the API's own error message. */
async function command(body: Record<string, unknown>): Promise<{ ok: true; value: unknown } | { ok: false; status: number; message: string }> {
  try {
    const response = await fetch("/actions/assistant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1, ...body }) });
    const value = await response.json().catch(() => null) as { error?: { message?: unknown } } | null;
    if (response.ok) return { ok: true, value };
    return { ok: false, status: response.status, message: typeof value?.error?.message === "string" ? value.error.message : FAILED };
  } catch { return { ok: false, status: 0, message: FAILED }; }
}

function reasonOf(overview: AssistantOverview | null): string | null {
  if (overview === null) return UNAVAILABLE_REASON.unavailable;
  if (overview.availability === "available") return null;
  return overview.reason ?? UNAVAILABLE_REASON[overview.availability];
}

/** Conversation state shared by the drawer and the Ask Signal page. */
function useAssistant(siteId: string, initialOverview: AssistantOverview | null | undefined, initialConversation: AssistantConversation | null) {
  const [overview, setOverview] = useState<AssistantOverview | null | undefined>(initialOverview);
  const [conversationId, setConversationId] = useState<string | null>(initialConversation?.conversation_id ?? null);
  const [messages, setMessages] = useState<AssistantMessage[]>(initialConversation?.messages ?? []);
  const [sending, setSending] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // A failed send keeps its request ids, so sending the same text again is a replay, not a second question.
  const retry = useRef<{ text: string; start: string; send: string } | null>(null);

  async function refresh(resume: boolean) {
    const value = await readJson(`/actions/assistant?site_id=${siteId}&resource=overview`) as AssistantOverview | null;
    setOverview(value);
    const latest = value?.conversations[0]?.conversation_id;
    if (resume && latest && conversationId === null) await open(latest);
  }

  async function open(id: string) {
    const value = await readJson(`/actions/assistant?site_id=${siteId}&resource=conversation&conversation_id=${id}`) as AssistantConversation | null;
    if (value === null) { setNotice("That conversation could not be loaded."); return; }
    setConversationId(value.conversation_id);
    setMessages(value.messages);
    setNotice(null);
  }

  function startNew() {
    setConversationId(null);
    setMessages([]);
    setNotice(null);
    retry.current = null;
  }

  async function send(raw: string): Promise<boolean> {
    const text = raw.trim();
    if (text === "" || sending !== null) return false;
    if (retry.current?.text !== text) retry.current = { text, start: crypto.randomUUID(), send: crypto.randomUUID() };
    const ids = retry.current;
    setSending(text);
    setNotice(null);
    let id = conversationId;
    if (id === null) {
      const started = await command({ site_id: siteId, action: "start", request_id: ids.start });
      if (!started.ok) { setSending(null); setNotice(started.message); return false; }
      id = (started.value as { conversation_id: string }).conversation_id;
      setConversationId(id);
    }
    const result = await command({ site_id: siteId, action: "send", conversation_id: id, request_id: ids.send, text });
    setSending(null);
    if (!result.ok) { setNotice(result.message); return false; }
    retry.current = null;
    const { owner_message, reply } = result.value as { owner_message: AssistantMessage; reply: AssistantMessage };
    setMessages((current) => [...current, owner_message, reply]);
    void refresh(false);
    return true;
  }

  return { overview, conversationId, messages, sending, notice, refresh, open, startNew, send };
}

function Message({ message }: { message: AssistantMessage }) {
  if (message.role === "owner") return <div className="c-msg me"><span>{message.text}</span></div>;
  const note = STATE_NOTE[message.state];
  return (
    <div className="c-msg sig">
      {note ? <span className="c-msg-state">{note}</span> : null}
      <span className="c-msg-text">{message.text}</span>
      {message.citations.length > 0 ? (
        <ul className="c-cites" aria-label="From these records">
          {message.citations.map((citation) => (
            <li key={`${citation.kind}:${citation.id}`}>{citation.href ? <a href={citation.href}>{citation.label}</a> : <span>{citation.label}</span>}</li>
          ))}
        </ul>
      ) : null}
      {message.actions.filter((action) => action.href !== null).map((action) => (
        <div className="c-msg-act" key={`${action.intent}:${action.href}`}>
          {action.confirm ? <span>{action.confirm}</span> : null}
          <a className="primary-command c-btn-sm" href={action.href!}>{action.label}</a>
        </div>
      ))}
      {message.remembered.map((memory) => (
        <a className="c-remembered" key={memory.memory_id} href="/chat#memory-title">Remembered: {memory.text}</a>
      ))}
    </div>
  );
}

/** Messages, suggestions and the composer. */
function Thread({ assistant, siteName, autoFocus }: { assistant: ReturnType<typeof useAssistant>; siteName: string; autoFocus?: boolean }) {
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const reason = assistant.overview === undefined ? null : reasonOf(assistant.overview);
  const asked = new Set(assistant.messages.filter((message) => message.role === "owner").map((message) => message.text));
  const suggestions = (assistant.overview?.suggestions ?? []).filter((item) => !asked.has(item.text));
  const disabled = reason !== null || assistant.overview === undefined || assistant.sending !== null;

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [assistant.messages.length, assistant.sending]);
  useEffect(() => { if (autoFocus && !disabled) input.current?.focus(); }, [autoFocus, disabled]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (await assistant.send(draft)) setDraft("");
  }

  return (
    <>
      <div className="c-msgs" aria-live="polite">
        <div className="c-msg sig"><span>I’m watching {siteName}. Ask about traffic, what I’m doing this week, or why I made a change. I answer only from what I’ve recorded.</span></div>
        {assistant.messages.map((message) => <Message key={message.message_id} message={message} />)}
        {assistant.sending ? (
          <>
            <div className="c-msg me"><span>{assistant.sending}</span></div>
            <div className="c-typing" role="status" aria-label="Signal is answering"><i /><i /><i /></div>
          </>
        ) : null}
        {assistant.overview === undefined ? <p className="c-msg-note">Loading…</p> : null}
        {reason ? <p className="c-msg-note" role="status">{reason}</p> : null}
        {assistant.notice ? <p className="c-msg-note" role="alert">{assistant.notice}</p> : null}
        <div ref={end} />
      </div>
      <form className="c-dfoot" onSubmit={(event) => void submit(event)}>
        {suggestions.length > 0 && !disabled ? (
          <div className="c-sug">{suggestions.map((item) => (
            <button type="button" key={item.text} onClick={() => void assistant.send(item.text)}>{item.text}</button>
          ))}</div>
        ) : null}
        <div className="c-comp">
          <input ref={input} className="c-dinp" placeholder="Ask about your site…" aria-label="Message" value={draft} maxLength={MAX_QUESTION} disabled={disabled}
            onChange={(event) => setDraft(event.target.value)} />
          <button type="submit" className="primary-command c-btn-sm" disabled={disabled || draft.trim() === ""}>Send</button>
        </div>
      </form>
    </>
  );
}

/** The top-bar command and its drawer. `#ask` in the address opens it, so any page can link to it. */
export function AskSignal({ siteId, siteName }: { siteId: string; siteName: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const loaded = useRef(false);
  const [opened, setOpened] = useState(false);
  const assistant = useAssistant(siteId, undefined, null);

  // Reads only refs, so the hash listener below may keep the first render's copy.
  function show() {
    if (!dialog.current?.open) dialog.current?.showModal();
    if (!loaded.current) { loaded.current = true; setOpened(true); void assistant.refresh(true); }
  }
  function close() {
    dialog.current?.close();
  }

  useEffect(() => {
    const fromHash = () => { if (window.location.hash === "#ask") show(); };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  return (
    <>
      <button type="button" className="c-cmd" aria-haspopup="dialog" aria-label="Ask Signal about your site" onClick={show}>
        <Search size={16} aria-hidden="true" />
        <span>Ask Signal about your site…</span>
      </button>
      <dialog ref={dialog} className="c-drawer" aria-label="Ask Signal"
        onClose={() => { if (window.location.hash === "#ask") history.replaceState(null, "", window.location.pathname + window.location.search); }}
        onClick={(event) => { if (event.target === dialog.current) close(); }}>
        <div className="c-dbar">
          <span className="c-ldot" aria-hidden="true" />
          <b>Ask Signal</b>
          {assistant.messages.length > 0 ? <button type="button" className="c-dbtn" onClick={assistant.startNew}>New conversation</button> : null}
          <a className="c-dbtn" href="/chat">All conversations</a>
          <button type="button" className="c-dx" onClick={close} aria-label="Close"><X size={18} aria-hidden="true" /></button>
        </div>
        {opened ? <Thread assistant={assistant} siteName={siteName} autoFocus /> : null}
      </dialog>
    </>
  );
}

const NOT_MEMORY = "Signal keeps only personal preferences (“I prefer…”, “Please keep…”) and plans (“I plan to…”, “I’m away…”). Facts about your business belong in Business facts, where you approve them.";
const KIND_LABEL: Record<AssistantMemory["kind"], string> = { preference: "Preference", context: "Plan", summary: "From a conversation" };

/** What Signal remembers about this site, with the owner's add and forget controls. */
function MemoryPanel({ siteId, initial }: { siteId: string; initial: AssistantMemory[] | null }) {
  const [memories, setMemories] = useState(initial);
  const [kind, setKind] = useState<"preference" | "context">("preference");
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const requestId = useRef<string | null>(null);

  async function remember(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (text === "") return;
    requestId.current ??= crypto.randomUUID();
    setBusy(true);
    const result = await command({ site_id: siteId, action: "remember", request_id: requestId.current, kind, text });
    setBusy(false);
    // The API keeps only recognisable personal preferences and plans; business claims go to Business facts.
    if (!result.ok) { setNotice(result.status === 409 ? NOT_MEMORY : result.message); return; }
    requestId.current = null;
    setDraft("");
    setNotice("Saved. Signal will use it in its answers.");
    const fresh = await readJson(`/actions/assistant?site_id=${siteId}&resource=memory`) as { memories: AssistantMemory[] } | null;
    if (fresh) setMemories(fresh.memories);
  }

  async function forget(id: string) {
    setBusy(true);
    const result = await command({ site_id: siteId, action: "forget", memory_id: id });
    setBusy(false);
    setConfirming(null);
    if (!result.ok) { setNotice(result.message); return; }
    setNotice("Forgotten. Signal won’t use it again.");
    setMemories((current) => (current ?? []).filter((memory) => memory.memory_id !== id));
  }

  return (
    <section className="c-pn c-memory" aria-labelledby="memory-title">
      <div className="c-pn-h"><h2 id="memory-title">What Signal remembers</h2><span className="c-ml">{memories === null ? "Unavailable" : `${memories.length} kept`}</span></div>
      {memories === null ? <p className="c-tb-empty">Memories can’t be read right now. Nothing has been forgotten.</p>
        : memories.length === 0 ? <p className="c-tb-empty">Nothing yet. Signal keeps what you tell it to remember, and short notes from your conversations.</p>
        : (
          <ul className="c-mem-list">
            {memories.map((memory) => (
              <li key={memory.memory_id}>
                <span className="c-pill o">{KIND_LABEL[memory.kind]}</span>
                <p>{memory.text}</p>
                <span className="c-ml">{new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(memory.created_at))}
                  {memory.source_conversation_id ? <> · <a href={`/chat?conversation=${memory.source_conversation_id}`}>From a conversation</a></> : null}</span>
                {confirming === memory.memory_id ? (
                  <span className="c-mem-confirm">
                    <button type="button" className="danger-command c-btn-sm" disabled={busy} onClick={() => void forget(memory.memory_id)}>Forget it</button>
                    <button type="button" className="secondary-command c-btn-sm" disabled={busy} onClick={() => setConfirming(null)}>Keep</button>
                  </span>
                ) : (
                  <button type="button" className="secondary-command c-btn-sm" disabled={busy} onClick={() => setConfirming(memory.memory_id)}>Forget</button>
                )}
              </li>
            ))}
          </ul>
        )}
      {memories !== null ? (
        <form className="c-mem-add" onSubmit={(event) => void remember(event)}>
          <label className="c-ml" htmlFor="memory-text">Tell Signal something to keep</label>
          <textarea id="memory-text" className="c-inp" value={draft} maxLength={MAX_MEMORY} rows={2} disabled={busy}
            placeholder={kind === "preference" ? "I prefer short answers with numbers." : "I'm away until October 20."} onChange={(event) => { setDraft(event.target.value); requestId.current = null; }} />
          <div className="c-mem-row">
            <select className="c-inp" aria-label="Kind" value={kind} disabled={busy} onChange={(event) => { setKind(event.target.value as "preference" | "context"); requestId.current = null; }}>
              <option value="preference">A preference (I prefer…, Please keep…)</option>
              <option value="context">A plan or time away (I plan to…, I’m away…)</option>
            </select>
            <button type="submit" className="primary-command c-btn-sm" disabled={busy || draft.trim() === ""}>Remember</button>
          </div>
        </form>
      ) : null}
      {notice ? <p role="status" className="c-okrow">{notice}</p> : null}
    </section>
  );
}

/** The Ask Signal page: past conversations, the open one, and the memory list. */
export function AskSignalPage({ siteId, siteName, overview, conversation, memories, telegramPaired }: {
  siteId: string; siteName: string;
  overview: AssistantOverview | null;
  conversation: AssistantConversation | null;
  memories: AssistantMemory[] | null;
  telegramPaired: boolean;
}) {
  const assistant = useAssistant(siteId, overview, conversation);
  const list = assistant.overview?.conversations ?? [];
  return (
    <>
      <header className="c-ph">
        <div>
          <span className="c-ml">Ask Signal · {list.length === 1 ? "1 conversation" : `${list.length} conversations`} · Telegram {telegramPaired ? "paired" : "not paired"}</span>
          <h1 className="c-h1"><span className="c-ln"><span>Ask about your site.</span></span><span className="c-ln"><span className="c-soft" style={{ animationDelay: ".1s" }}>Signal answers from its records.</span></span></h1>
        </div>
      </header>
      <div className="c-chat">
        <div className="c-col">
          <section className="c-pn" aria-labelledby="conversations-title">
            <div className="c-pn-h"><h2 id="conversations-title">Conversations</h2><button type="button" className="secondary-command c-btn-sm" onClick={assistant.startNew}>New</button></div>
            {list.length === 0 ? <p className="c-tb-empty">No conversations yet. Ask your first question.</p> : (
              <ul className="c-conv-list">
                {list.map((item) => (
                  <li key={item.conversation_id}>
                    <a href={`/chat?conversation=${item.conversation_id}`} aria-current={item.conversation_id === assistant.conversationId ? "page" : undefined}
                      onClick={(event) => { event.preventDefault(); history.replaceState(null, "", `/chat?conversation=${item.conversation_id}`); void assistant.open(item.conversation_id); }}>
                      <b>{item.title ?? "Untitled conversation"}</b>
                      <span className="c-ml">{new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(item.updated_at))} · {item.message_count} {item.message_count === 1 ? "message" : "messages"}</span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <MemoryPanel siteId={siteId} initial={memories} />
        </div>
        <section className="c-thread" aria-label="Conversation">
          <Thread assistant={assistant} siteName={siteName} />
        </section>
      </div>
    </>
  );
}
