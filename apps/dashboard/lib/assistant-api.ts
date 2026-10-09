import { boundedRelayText } from "./relay-json";
import { relayJson } from "./relay-json";
import { validatedSignalApiBaseUrl } from "./api-origin";
import { TENANT_COOKIE_NAME } from "./browser-auth";
import { validBrandDocumentId as uuid } from "./brand-document-api";

/* Ask Signal: questions about the owner's own site, answered from its records,
   with conversations and memories kept by the API. The dashboard only relays
   and validates; every answer, citation and memory comes from the API. */

export const ASSISTANT_STATES = ["answered", "no_record", "unavailable", "failed", "outcome_unknown"] as const;
const AVAILABILITY = ["available", "model_unconfigured", "budget_exhausted", "unavailable"] as const;
const CITATION_KINDS = ["revision", "article", "fact", "operation", "observation", "measurement", "weekly_report", "strategy_item", "topic", "visibility_question", "health_check", "memory"] as const;
const INTENTS = ["open_revision", "open_article", "open_fact", "pause_loop", "resume_loop", "open_autonomy", "open_connections", "unsupported"] as const;
const MEMORY_KINDS = ["preference", "context", "summary"] as const;
export const MAX_QUESTION = 2000;
export const MAX_MEMORY = 500;
const MAX_BYTES = 256 * 1024;

export interface AssistantCitation { kind: (typeof CITATION_KINDS)[number]; id: string; label: string; href: string | null }
export interface AssistantAction { intent: (typeof INTENTS)[number]; label: string; href: string | null; confirm: string | null }
export interface AssistantMessage {
  message_id: string; role: "owner" | "signal"; text: string; created_at: string;
  state: (typeof ASSISTANT_STATES)[number];
  citations: AssistantCitation[]; actions: AssistantAction[]; remembered: { memory_id: string; text: string }[];
}
export interface AssistantConversationSummary { conversation_id: string; title: string | null; updated_at: string; message_count: number }
export interface AssistantOverview {
  schema_version: 1; availability: (typeof AVAILABILITY)[number]; reason: string | null;
  suggestions: { text: string }[]; conversations: AssistantConversationSummary[];
}
export interface AssistantConversation { schema_version: 1; conversation_id: string; title: string | null; messages: AssistantMessage[] }
export interface AssistantMemory {
  memory_id: string; kind: (typeof MEMORY_KINDS)[number]; text: string; created_at: string;
  source_conversation_id: string | null; source_message_id: string | null;
}
export interface AssistantMemories { schema_version: 1; memories: AssistantMemory[] }

function exact(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}
const text = (value: unknown, max: number, empty = false): value is string =>
  typeof value === "string" && value.length <= max && (empty || value.trim() !== "") && !/[\0-\x08\x0b\x0c\x0e-\x1f]/.test(value);
const date = (value: unknown) => typeof value === "string" && !Number.isNaN(Date.parse(value));
const oneOf = <T extends readonly string[]>(list: T, value: unknown): value is T[number] => list.includes(String(value));
const list = (value: unknown, max: number, item: (entry: unknown) => boolean) => Array.isArray(value) && value.length <= max && value.every(item);
const nullable = <T>(value: unknown, check: (entry: unknown) => entry is T) => value === null || check(value);

/** A same-origin, relative dashboard path. Anything else (absolute, protocol-relative, scripts) is refused. */
export function safeHref(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 512 || /[\s\\]/.test(value) || !/^\/(?:[a-z-]{1,32})?(?:[?#]|$)/.test(value)) return false;
  try { return new URL(value, "https://dashboard.invalid").origin === "https://dashboard.invalid"; } catch { return false; }
}

function validMessage(value: unknown): value is AssistantMessage {
  if (!exact(value, ["message_id", "role", "text", "created_at", "state", "citations", "actions", "remembered"])) return false;
  if (!uuid(value.message_id) || !oneOf(["owner", "signal"] as const, value.role) || !text(value.text, 8000) || !date(value.created_at) || !oneOf(ASSISTANT_STATES, value.state)) return false;
  const citation = (item: unknown) => exact(item, ["kind", "id", "label", "href"]) && oneOf(CITATION_KINDS, item.kind) && text(item.id, 128) && text(item.label, 200) && nullable(item.href, safeHref);
  const action = (item: unknown) => exact(item, ["intent", "label", "href", "confirm"]) && oneOf(INTENTS, item.intent) && text(item.label, 80) && nullable(item.href, safeHref) && nullable(item.confirm, (entry): entry is string => text(entry, 300));
  const remembered = (item: unknown) => exact(item, ["memory_id", "text"]) && uuid(item.memory_id) && text(item.text, MAX_MEMORY);
  if (!list(value.citations, 20, citation) || !list(value.actions, 6, action) || !list(value.remembered, 10, remembered)) return false;
  return value.role === "signal" || (value.citations as unknown[]).length + (value.actions as unknown[]).length + (value.remembered as unknown[]).length === 0;
}

function validMemory(value: unknown): value is AssistantMemory {
  return exact(value, ["memory_id", "kind", "text", "created_at", "source_conversation_id", "source_message_id"]) && uuid(value.memory_id) && oneOf(MEMORY_KINDS, value.kind)
    && text(value.text, MAX_MEMORY) && date(value.created_at) && (value.source_conversation_id === null || uuid(value.source_conversation_id)) && (value.source_message_id === null || uuid(value.source_message_id));
}

export function validOverview(value: unknown): value is AssistantOverview {
  return exact(value, ["schema_version", "availability", "reason", "suggestions", "conversations"]) && value.schema_version === 1 && oneOf(AVAILABILITY, value.availability)
    && nullable(value.reason, (entry): entry is string => text(entry, 300))
    && list(value.suggestions, 4, (item) => exact(item, ["text"]) && text(item.text, 200))
    && list(value.conversations, 20, (item) => exact(item, ["conversation_id", "title", "updated_at", "message_count"]) && uuid(item.conversation_id)
      && nullable(item.title, (entry): entry is string => text(entry, 200)) && date(item.updated_at) && Number.isSafeInteger(item.message_count) && Number(item.message_count) >= 0);
}

export function validConversation(value: unknown): value is AssistantConversation {
  return exact(value, ["schema_version", "conversation_id", "title", "messages"]) && value.schema_version === 1 && uuid(value.conversation_id)
    && nullable(value.title, (entry): entry is string => text(entry, 200)) && list(value.messages, 200, validMessage);
}

export function validMemories(value: unknown): value is AssistantMemories {
  return exact(value, ["schema_version", "memories"]) && value.schema_version === 1 && list(value.memories, 200, validMemory);
}

export function validReply(value: unknown): value is { schema_version: 1; owner_message: AssistantMessage; reply: AssistantMessage } {
  return exact(value, ["schema_version", "owner_message", "reply"]) && value.schema_version === 1 && validMessage(value.owner_message) && validMessage(value.reply)
    && value.owner_message.role === "owner" && value.reply.role === "signal";
}

type Command = { siteId: string; path: string; body: Record<string, unknown>; valid: (value: unknown) => boolean; timeout: number };

/** The four owner commands, checked field by field before anything is relayed. */
export function assistantCommand(value: unknown): Command | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  if (input.schema_version !== 1 || !uuid(input.site_id)) return null;
  const base = ["schema_version", "site_id", "action"];
  if (input.action === "start" && exact(input, [...base, "request_id"]) && uuid(input.request_id)) {
    return { siteId: input.site_id, path: "conversations", body: { schema_version: 1, request_id: input.request_id }, timeout: 15000,
      valid: (item) => exact(item, ["schema_version", "conversation_id", "created_at"]) && item.schema_version === 1 && uuid(item.conversation_id) && date(item.created_at) };
  }
  if (input.action === "send" && exact(input, [...base, "conversation_id", "request_id", "text"]) && uuid(input.conversation_id) && uuid(input.request_id) && text(input.text, MAX_QUESTION)) {
    return { siteId: input.site_id, path: `conversations/${input.conversation_id}/messages`, body: { schema_version: 1, request_id: input.request_id, text: input.text }, timeout: 90000, valid: validReply };
  }
  if (input.action === "remember" && exact(input, [...base, "request_id", "kind", "text"]) && uuid(input.request_id) && oneOf(["preference", "context"] as const, input.kind) && text(input.text, MAX_MEMORY)) {
    return { siteId: input.site_id, path: "memory", body: { schema_version: 1, request_id: input.request_id, kind: input.kind, text: input.text }, timeout: 15000,
      valid: (item) => validMemory(item) || (typeof item === "object" && item !== null && "schema_version" in item && item.schema_version === 1 && validMemory(Object.fromEntries(Object.entries(item).filter(([key]) => key !== "schema_version")))) };
  }
  if (input.action === "forget" && exact(input, [...base, "memory_id"]) && uuid(input.memory_id)) {
    return { siteId: input.site_id, path: `memory/${input.memory_id}/forget`, body: { schema_version: 1 }, timeout: 15000,
      valid: (item) => exact(item, ["schema_version", "state"]) && item.schema_version === 1 && item.state === "forgotten" };
  }
  return null;
}

/** GET resources the browser may read: the overview, one conversation, or the memory list. */
export function assistantResource(resource: string | null, conversationId: string | null): { path: string; valid: (value: unknown) => boolean } | null {
  if (resource === "overview") return { path: "", valid: validOverview };
  if (resource === "memory") return { path: "memory", valid: validMemories };
  if (resource === "conversation" && uuid(conversationId)) return { path: `conversations/${conversationId}`, valid: validConversation };
  return null;
}

const validError = (value: unknown) => exact(value, ["error"]) && exact(value.error, ["code", "message", "retryable", "correlation_id"])
  && text(value.error.code, 64) && text(value.error.message, 200) && typeof value.error.retryable === "boolean" && text(value.error.correlation_id, 64);

export async function relayAssistant(token: string, siteId: string, origin: string, read: { path: string; valid: (value: unknown) => boolean } | null, command?: Command): Promise<Response> {
  const target = command ?? read;
  if (!/^[A-Za-z0-9_-]{43}$/.test(token) || !uuid(siteId) || !target) return Response.json({ state: "rejected" }, { status: 403 });
  try {
    const base = validatedSignalApiBaseUrl(process.env.SIGNAL_API_BASE_URL ?? "http://127.0.0.1:8000");
    const cookie = `${TENANT_COOKIE_NAME}=${token}`;
    let csrf = "";
    if (command) {
      const response = await relayJson(new URL("/v1/session/tenant-csrf", base), { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(2500), headers: { Cookie: cookie, Accept: "application/json" } }, globalThis.fetch, 262144);
      const value: unknown = await response.json();
      if (response.status !== 200 || response.headers.getSetCookie().length || !exact(value, ["schema_version", "csrf_token"]) || value.schema_version !== 1 || typeof value.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.csrf_token)) throw new Error();
      csrf = value.csrf_token;
    }
    const path = `/v1/sites/${siteId}/assistant${target.path ? `/${target.path}` : ""}`;
    const response = await relayJson(new URL(path, base), {
      method: command ? "POST" : "GET", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(command?.timeout ?? 15000),
      headers: { Cookie: cookie, Accept: "application/json", ...(command ? { Origin: origin, "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf, "Content-Type": "application/json" } : {}) },
      ...(command ? { body: JSON.stringify(command.body) } : {}),
    }, globalThis.fetch, 262144);
    if (response.headers.getSetCookie().length) throw new Error();
    const raw = await boundedRelayText(response, MAX_BYTES);
    if (new TextEncoder().encode(raw).length > MAX_BYTES) throw new Error();
    const value: unknown = JSON.parse(raw);
    if (response.ok ? !target.valid(value) : !validError(value)) throw new Error();
    return Response.json(value, { status: response.status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
  } catch { return Response.json({ state: "unavailable" }, { status: 503 }); }
}

export interface AssistantPage { overview: AssistantOverview | null; conversation: AssistantConversation | null; memories: AssistantMemory[] | null }
export const NO_ASSISTANT: AssistantPage = { overview: null, conversation: null, memories: null };

async function readValid<T>(response: Response): Promise<T | null> {
  return response.status === 200 ? await response.json() as T : null;
}

/** Server-side read for the Ask Signal page through the same relay and validation as the browser route. */
export async function loadAssistantPage(token: string, siteId: string, requested: unknown): Promise<AssistantPage> {
  const [overview, memory] = await Promise.all([
    relayAssistant(token, siteId, "", assistantResource("overview", null)).then((response) => readValid<AssistantOverview>(response)),
    relayAssistant(token, siteId, "", assistantResource("memory", null)).then((response) => readValid<AssistantMemories>(response)),
  ]);
  const id = uuid(requested) ? requested : overview?.conversations[0]?.conversation_id ?? null;
  const conversation = id === null ? null : await readValid<AssistantConversation>(await relayAssistant(token, siteId, "", assistantResource("conversation", id)));
  return { overview, conversation, memories: memory?.memories ?? null };
}
