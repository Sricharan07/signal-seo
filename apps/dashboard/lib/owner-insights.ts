import { validVisibilityData, relayVisibility, type VisibilityData } from "./ai-visibility-api";
import { relayBrain, type BrainFact } from "./business-brain-api";
import { relayWriter, validWriterData, type WriterData } from "./content-writer-api";
import { relayStrategy, validSeoProjection, type SeoProjection } from "./seo-strategy-api";

/* Server-side reads for the owner views. Each reuses the exact relay the
   browser routes use (same API call, size bound and strict validation), so
   rendering on the server adds no new trust surface. */

export type Insight<T> = { state: "available"; value: T } | { state: "unavailable" };

export interface OwnerInsights {
  seo: Insight<SeoProjection>;
  visibility: Insight<VisibilityData>;
  writer: Insight<WriterData>;
  facts: Insight<BrainFact[]>;
}

export const NO_INSIGHTS: OwnerInsights = {
  seo: { state: "unavailable" }, visibility: { state: "unavailable" }, writer: { state: "unavailable" }, facts: { state: "unavailable" },
};

const UNAVAILABLE = { state: "unavailable" } as const;

async function read<T>(response: Response, valid: (value: unknown) => value is T): Promise<Insight<T>> {
  if (response.status !== 200) return UNAVAILABLE;
  try {
    const value: unknown = await response.json();
    return valid(value) ? { state: "available", value } : UNAVAILABLE;
  } catch {
    return UNAVAILABLE;
  }
}

export async function loadSeoInsight(token: string, siteId: string): Promise<Insight<SeoProjection>> {
  return read(await relayStrategy(token, siteId, ""), validSeoProjection);
}

export async function loadVisibilityInsight(token: string, siteId: string): Promise<Insight<VisibilityData>> {
  return read(await relayVisibility(token, siteId, ""), validVisibilityData);
}

export async function loadWriterInsight(token: string, siteId: string): Promise<Insight<WriterData>> {
  return read(await relayWriter(token, siteId, ""), validWriterData);
}

const validFacts = (value: unknown): value is { facts: BrainFact[] } =>
  typeof value === "object" && value !== null && Array.isArray((value as { facts?: unknown }).facts);

export async function loadFactsInsight(token: string, siteId: string): Promise<Insight<BrainFact[]>> {
  const result = await read(await relayBrain(token, siteId, "", "facts"), validFacts);
  return result.state === "available" ? { state: "available", value: result.value.facts } : UNAVAILABLE;
}
