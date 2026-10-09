"use client";

import { LoaderCircle, ShieldCheck } from "lucide-react";
import { useState } from "react";

import type { StandingAuthorizationState, StandingMutationResult } from "../lib/standing-authorization-api";
import { readableCode } from "./home-panels";

const GRANT_STATES: Record<string, string> = {
  no_grant: "No grant", active: "Active", not_started: "Not started yet", revoked: "Revoked",
  expired: "Expired", recovery_stale: "Needs a fresh grant",
};
const WORK_TYPES: Record<string, string> = {
  draft_patch: "Draft and sandbox patches", metadata_pr: "Title and description pull requests",
  new_article: "New articles", content_refresh: "Content refreshes", research_audit: "Research and audits",
};
const dollars = (cents: number) =>
  new Intl.NumberFormat("en", { style: "currency", currency: "USD" }).format(cents / 100);

export function StandingAuthorization({
  siteId,
  siteName,
  state,
  verified,
}: {
  siteId: string | null;
  siteName: string | null;
  state: StandingAuthorizationState;
  verified: boolean;
}) {
  const [busy, setBusy] = useState<"grant" | "revoke" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [threshold, setThreshold] = useState("0.9");
  const [volume, setVolume] = useState("2");
  const [spend, setSpend] = useState("0");
  const [excluded, setExcluded] = useState("");
  const [pending, setPending] = useState<"small" | null>(null);
  const grant = state.state === "available" ? state.grant : null;
  const canGrant = siteId !== null && verified && grant !== null &&
    !["active", "not_started"].includes(grant.state);
  const canRevoke = siteId !== null && grant?.grantId !== null &&
    grant?.state !== "revoked" && grant?.state !== "expired";

  async function submitGrant(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canGrant || siteId === null) return;
    const thresholdValue = Number(threshold);
    const volumeValue = Number(volume);
    const spendValue = Number(spend);
    const paths = excluded.split(/[\n,]/).map((path) => path.trim()).filter(Boolean);
    if (!Number.isFinite(thresholdValue) || thresholdValue < 0 || thresholdValue > 1 ||
        !Number.isInteger(volumeValue) || volumeValue < 1 || volumeValue > 1000 ||
        !Number.isInteger(spendValue) || spendValue < 0 || spendValue > 100000000 ||
        paths.length > 64 || paths.some((path) => !/^\/[A-Za-z0-9_./-]{0,1023}$/.test(path) || path.includes("..") || path.includes("//"))) {
      setMessage("Check the threshold, weekly caps, and excluded paths.");
      return;
    }
    setBusy("grant");
    setMessage(null);
    const startsAt = new Date();
    const endsAt = new Date(startsAt.getTime() + 7 * 24 * 60 * 60 * 1000);
    try {
      const response = await fetch("/auth/grant-standing", {
        method: "POST", credentials: "same-origin", signal: AbortSignal.timeout(18_000),
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: 1, site_id: siteId,
          recipe_ranges: [{ key: "title_description_improvement", minimum_inclusive: "1.0.0", maximum_exclusive: "1.0.1" }],
          thresholds: { draft_patch: thresholdValue },
          weekly_volume_caps: { draft_patch: volumeValue },
          weekly_total_cap: volumeValue,
          weekly_spend_cents: spendValue,
          excluded_paths: paths,
          starts_at: startsAt.toISOString(), ends_at: endsAt.toISOString(),
          recovery_window_hours: 24,
        }),
      });
      const result = await response.json() as StandingMutationResult;
      if (response.status === 201 && result.state === "recorded") {
        window.location.reload();
      } else {
        setMessage(failure(result.state));
      }
    } catch {
      setMessage("The grant service is unavailable. No authorization was recorded.");
    } finally {
      setBusy(null);
    }
  }

  async function submitRevoke() {
    if (!canRevoke || siteId === null || grant?.grantId == null) return;
    setBusy("revoke");
    setMessage(null);
    try {
      const response = await fetch("/auth/revoke-standing", {
        method: "POST", credentials: "same-origin", signal: AbortSignal.timeout(18_000),
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ schema_version: 1, site_id: siteId, grant_id: grant.grantId }),
      });
      const result = await response.json() as StandingMutationResult;
      if (response.status === 202 && result.state === "revoked") {
        window.location.reload();
      } else {
        setMessage(failure(result.state));
      }
    } catch {
      setMessage("The revocation service is unavailable. Check current authority before retrying.");
    } finally {
      setBusy(null);
    }
  }

  const active = state.state === "available" && grant !== null && grant.state === "active";
  const current: "ask" | "small" = active ? "small" : "ask";
  const chosen = pending ?? current;
  const levels = [
    { key: "ask" as const, kicker: "Level 1", title: "Ask me first", desc: "Signal researches and prepares everything. Nothing ships without your approval.", tag: "Every change comes to you", tone: "p1", available: true },
    { key: "small" as const, kicker: "Level 2", title: "Small fixes on its own", desc: "Signal drafts title and description fixes on its own, within your weekly limits. Pull requests still need your approval.", tag: "Recommended", tone: "p2", available: verified && siteId !== null && state.state === "available" },
    { key: "more" as const, kicker: "Level 3", title: "Fixes and refreshes", desc: "Also refreshes existing articles from your approved facts. New articles still come to you.", tag: "Not available yet", tone: "p3", available: false },
  ];
  function pick(key: "ask" | "small" | "more") {
    setMessage(null);
    if (key === "more" || key === current) { setPending(null); return; }
    if (key === "ask") { setPending(null); if (canRevoke) void submitRevoke(); return; }
    setPending(key);
  }
  return (
    <section className="standing-authorization c-autonomy" aria-labelledby="standing-title">
      <h2 id="standing-title" className="c-visually-hidden">Standing authorization for {siteName ?? "this site"}</h2>
      {siteId === null ? (
        <p className="standing-message">Select a site to inspect its authorization.</p>
      ) : state.state !== "available" ? (
        <p className="standing-message">Current grant state could not be verified. No new authority is assumed.</p>
      ) : !verified ? (
        <p className="standing-message">Site ownership must be verified before an owner can grant standing authority.</p>
      ) : null}
      <div className="c-prods" role="radiogroup" aria-label="Autonomy level">
        {levels.map((level, index) => (
          <button key={level.key} type="button" role="radio" aria-checked={chosen === level.key} disabled={!level.available || busy !== null}
            className={`c-prod c-rs ${level.tone}${chosen === level.key ? " sel" : ""}`} style={{ animationDelay: `${0.08 + index * 0.07}s` }} onClick={() => pick(level.key)}>
            <span className="c-ml c-prod-k">{level.kicker}</span>
            <span className="t3">{level.title}</span>
            <span className="pp">{level.desc}</span>
            <span className="f"><span className="c-prod-tag">{level.tag}</span><span className="c-pick">{current === level.key ? "Current" : chosen === level.key ? "Selected" : level.available ? "Choose" : "Unavailable"}</span></span>
          </button>
        ))}
      </div>
      {pending === "small" && canGrant ? (
        <form className="c-confirm" onSubmit={submitGrant}>
          <p><b>Change to “Small fixes on its own”?</b> Giving Signal more room needs a fresh 2-step verification check. Valid for seven days; recovery window 24 hours.</p>
          <button type="button" className="secondary-command" onClick={() => setPending(null)} disabled={busy !== null}>Cancel</button>
          <button type="submit" className="primary-command" disabled={busy !== null}>
            {busy === "grant" ? <LoaderCircle size={16} aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}
            Verify and change
          </button>
        </form>
      ) : null}
      {message ? <p className="standing-message" role="status">{message}</p> : null}
      <section className="c-pn c-in" aria-label="Limits">
        <div className="c-pn-h"><b>Limits</b><span className="c-note">Applied every week</span></div>
        {active && grant ? (
          <div className="c-lims">
            <div className="c-lim"><div className="c-lim-top"><span>Changes Signal may make per week</span><b>{grant.weeklyTotalCap ?? "—"}</b></div><span className="c-note">Set when you raised the level. Lower it to Ask me first and raise it again to change limits.</span></div>
            <div className="c-lim"><div className="c-lim-top"><span>Weekly spend on AI and data</span><b>{grant.weeklySpendCents === null ? "—" : dollars(grant.weeklySpendCents)}</b></div><span className="c-note">Paid work pauses at the limit. Ends {grant.endsAt ? new Date(grant.endsAt).toLocaleDateString("en", { month: "short", day: "numeric" }) : "when revoked"}.</span></div>
          </div>
        ) : (
          <div className="c-lims">
            <label className="c-lim"><div className="c-lim-top"><span>Changes Signal may make per week</span><b>{volume}</b></div>
              <input className="c-rng" type="range" min="1" max="25" step="1" value={Number(volume)} onChange={(event) => setVolume(event.target.value)} />
              <span className="c-note">Used when you raise the level. Articles always come to you.</span></label>
            <label className="c-lim"><div className="c-lim-top"><span>Weekly spend on AI and data</span><b>{dollars(Number(spend) || 0)}</b></div>
              <input className="c-rng" type="range" min="0" max="20000" step="100" value={Number(spend) || 0} onChange={(event) => setSpend(event.target.value)} />
              <span className="c-note">Paid work pauses at the limit.</span></label>
          </div>
        )}
        {!active ? (
          <details className="technical-details c-advanced">
            <summary>Advanced</summary>
            <div className="standing-fields">
              <label><span>Confidence threshold</span><input type="number" min="0" max="1" step="0.01" required value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
              <label className="standing-paths"><span>Pages Signal must never touch, one path per line</span><textarea value={excluded} maxLength={4096} onChange={(event) => setExcluded(event.target.value)} placeholder="/private" /></label>
            </div>
            <p className="c-note">Reviewed draft recipe 1.0.0 (<code>9f2c4164-64a6-4e19-a2d0-3d9b16c8e701</code>). Pull requests and production writes remain unavailable.</p>
          </details>
        ) : null}
        {grant?.grantId !== null && grant?.grantId !== undefined ? (
          <details className="technical-details standing-technical">
            <summary>Technical details</summary>
            <p>Grant <code>{grant.grantId}</code></p>
            <p>Work type codes <code>{grant.workTypes.join(", ")}</code> ({grant.workTypes.map((type) => WORK_TYPES[type] ?? readableCode(type)).join(", ")})</p>
            {grant.durability === "AUTHORITY_DURABILITY_PENDING" ? <p role="status">Revoked locally. Independent durability is still pending.</p> : null}
          </details>
        ) : null}
        <div className="c-guard">
          <div><b>Never merges</b>Every change arrives as a pull request. You merge, you deploy.</div>
          <div><b>Only your facts</b>Signal writes only claims you’ve confirmed. Anything new comes to you.</div>
          <div><b>Always asks for</b>Pricing, legal and customer-named pages, new articles, and big changes.</div>
        </div>
      </section>
    </section>
  );
}

function failure(state: StandingMutationResult["state"]): string {
  if (state === "conflict") return "The site or recipe authority changed. Refresh and review it again.";
  if (state === "rejected") return "This session is not allowed to change standing authority.";
  return "The authority service is unavailable. No success was assumed.";
}
