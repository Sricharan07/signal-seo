import type { ReactNode } from "react";

import type { DataForSeoState } from "../lib/dataforseo-api";
import type { DocsState } from "../lib/docs-api";
import type { IndexNowState } from "../lib/indexnow-api";
import type { BingState, GithubState, GscState } from "../lib/owner-connectors-api";
import type { SlackState } from "../lib/slack-api";
import type { TelegramState } from "../lib/telegram-api";
import { ConnectionStatus } from "./connection-status";

/* Connections as the prototype's tiles: what each tool is for, what is
   connected, and its state. Each tile keeps the existing controls inside. */

export interface Summary { detail: string; status: string | null }

export const summaries = {
  gsc: (state: GscState): Summary => state.availability === "bound" ? { detail: state.property_resource_name, status: "Connected" }
    : state.availability === "reauth_required" ? { detail: state.property_resource_name, status: "Reconnect required" }
    : state.availability === "selecting" ? { detail: "Choose your property", status: "Confirmation required" }
    : state.availability === "unbound" ? { detail: "Not connected", status: "Not connected" } : { detail: "Unavailable here", status: "Unavailable" },
  github: (state: GithubState): Summary => "binding_id" in state
    ? { detail: `${state.owner}/${state.repository}`, status: state.availability === "active" ? "Connected" : state.availability === "prepared" ? "Verification incomplete" : state.availability === "failed" ? "Verification failed" : "Reconnect required" }
    : state.availability === "unbound" ? { detail: "Not connected", status: "Not connected" } : { detail: "Unavailable here", status: "Unavailable" },
  bing: (state: BingState): Summary => state.availability === "bound" ? { detail: state.site_url, status: "Connected" }
    : state.availability === "reauth_required" ? { detail: state.site_url, status: "Reconnect required" }
    : state.availability === "selecting" ? { detail: "Choose your site", status: "Confirmation required" }
    : state.availability === "unbound" ? { detail: "Not connected", status: "Not connected" } : { detail: "Unavailable here", status: "Unavailable" },
  slack: (state: SlackState): Summary => state.availability === "bound" ? { detail: `Channel ${state.channel_id}`, status: "Connected" }
    : state.availability === "unbound" ? { detail: "Not set up", status: "Not connected" } : { detail: "Not configured here", status: "Unavailable" },
  telegram: (state: TelegramState): Summary => state.availability === "bound" ? { detail: state.link_id ? "Paired with you" : `@${state.bot_username ?? "bot"}`, status: "Connected" }
    : state.availability === "failed" ? { detail: "Setup not confirmed", status: "Setup not confirmed" }
    : state.availability === "unbound" ? { detail: "Not set up", status: "Not connected" } : { detail: "Not configured here", status: "Unavailable" },
  dataforseo: (state: DataForSeoState): Summary => {
    const usd = (micros: number) => new Intl.NumberFormat("en", { style: "currency", currency: "USD" }).format(micros / 1_000_000);
    if (state.availability === "available") return { detail: `${usd(state.usage_micros)} of ${usd(state.cap_micros)} this month`, status: "Available" };
    if (state.availability === "cap_exhausted") return { detail: `${usd(state.cap_micros)} cap reached this month`, status: "Monthly cap exhausted" };
    if (state.availability === "unconfigured") return { detail: "Not connected", status: "Not configured" };
    return { detail: "Unavailable here", status: "Unavailable" };
  },
  docs: (state: DocsState): Summary => state.availability === "ready" ? { detail: `${state.sources.length} ${state.sources.length === 1 ? "document" : "documents"}`, status: "Connected" }
    : state.availability === "degraded" ? { detail: "Access changed", status: "Reconnect required" }
    : state.availability === "unbound" || state.availability === "revoked" ? { detail: "Not connected", status: "Not connected" } : { detail: "Unavailable here", status: "Unavailable" },
  indexnow: (state: IndexNowState): Summary => state.state !== "available" ? { detail: "Unavailable here", status: state.state === "rejected" ? "Owner access required" : "Unavailable" }
    : { detail: { not_created: "Key file not created", pr_open: "Key file waiting to merge", deployed: "Key file live", mismatch: "Key file mismatch" }[state.keyStatus], status: state.keyStatus === "deployed" ? "Available" : state.keyStatus === "not_created" ? "Not created" : "Needs attention" },
};

export function ConnectionsHeader() {
  return (
    <header className="c-ph">
      <div>
        <span className="c-ml">Connections · narrowest access</span>
        <h1 className="c-h1"><span className="c-ln"><span>Read-only wherever possible.</span></span></h1>
      </div>
      <span className="c-ph-note">GitHub can open pull requests. Nothing can merge, deploy or read your secrets.</span>
    </header>
  );
}

export function ConnectionTileGroup({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <section className="c-cgroup" aria-label={title}>
      <div className="c-gh"><b>{title}</b><span>{detail}</span></div>
      <div className="c-ctiles">{children}</div>
    </section>
  );
}

export function ConnectionTile({ mono, name, desc, summary, children }: { mono: string; name: string; desc: string; summary: Summary | null; children?: ReactNode }) {
  const off = summary?.status === "Not connected" || summary?.status === "Not configured" || summary?.status === "Not created";
  return (
    <div className="c-ct c-rs">
      <div className="c-ct-top"><span className="c-logo" aria-hidden="true">{mono}</span><div><div className="c-ct-n">{name}</div><div className="c-ct-d">{desc}</div></div></div>
      {children ? (
        <details className="c-ct-manage">
          <summary>{off ? "Connect" : "Manage"}</summary>
          <div className="c-ct-body">{children}</div>
        </details>
      ) : null}
      <div className="c-ct-f"><span>{summary?.detail ?? "Open to see its state"}</span>{summary?.status ? <ConnectionStatus>{summary.status}</ConnectionStatus> : null}</div>
    </div>
  );
}
