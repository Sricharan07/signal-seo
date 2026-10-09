/* Shared presentation for connection cards: a worded status pill whose tone
   follows the words, and the approval limit in plain language. */

const READY = new Set(["Connected", "Available", "Read only"]);
const OFF = new Set(["Not connected", "Not configured", "Disconnected", "Not created", "Unavailable", "Owner access required"]);

export function ConnectionStatus({ children }: { children: string }) {
  const tone = READY.has(children) ? "ready" : OFF.has(children) ? "off" : "attention";
  return <span className="connection-status" data-tone={tone}>{children}</span>;
}

const LIMITS = ["Research only", "Drafts", "Pull requests"];

/** A0–A2 as the owner reads them; the code stays visible for exactness. */
export function approvalLimit(level: number): string {
  return `${LIMITS[level] ?? "Unknown"} (A${level})`;
}
