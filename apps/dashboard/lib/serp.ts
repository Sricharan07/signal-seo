/* What a search result shows for a page: its title and description, read from
   the exact sealed before and after fragments. Only those two fields are read;
   anything else in the fragment is ignored and never rendered as HTML. */

export interface SerpSide { title: string | null; description: string | null }
export interface SerpPreview { before: SerpSide; after: SerpSide; titleChanged: boolean; descriptionChanged: boolean }

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: " " };

export function decodeEntities(value: string): string {
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, code: string) => {
    if (code[0] === "#") {
      const point = code[1].toLowerCase() === "x" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(point) && point > 0 && point <= 0x10ffff ? String.fromCodePoint(point) : match;
    }
    return ENTITIES[code.toLowerCase()] ?? match;
  });
}

function read(fragment: string): SerpSide {
  const title = fragment.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1];
  const meta = [...fragment.matchAll(/<meta\b[^>]*>/gi)].map((match) => match[0])
    .find((tag) => /\bname\s*=\s*["']description["']/i.test(tag));
  const content = meta?.match(/\bcontent\s*=\s*(["'])([\s\S]*?)\1/i)?.[2];
  const clean = (value: string | undefined) => value === undefined ? null : decodeEntities(value).replace(/\s+/g, " ").trim();
  return { title: clean(title), description: clean(content) };
}

/** A search-result preview when the change touches the title or description; otherwise null. */
export function serpPreview(before: string, after: string): SerpPreview | null {
  const now = read(before), next = read(after);
  const titleChanged = now.title !== next.title && next.title !== null;
  const descriptionChanged = now.description !== next.description && next.description !== null;
  if (!titleChanged && !descriptionChanged) return null;
  return { before: now, after: next, titleChanged, descriptionChanged };
}

/** "docs.example.test › pricing" as search results show the URL. */
export function serpCrumb(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.split("/").filter(Boolean);
    return [parsed.host, ...path].join(" › ");
  } catch {
    return url;
  }
}
