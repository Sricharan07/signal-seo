"use client";

import { Ban, Check, GitBranch, Link, LoaderCircle, RefreshCw, Search, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { ConnectionStatus } from "./connection-status";
import { readableCode } from "./home-panels";
import { bingAuthorizationUrl, gscAuthorizationUrl, type BingState, type GscState, type GithubState, type GithubPrState, type OwnerConnector } from "../lib/owner-connectors-api";

function useConnector(connector: OwnerConnector, siteId: string | null) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  async function command(body: Record<string, unknown>) {
    if (busy || siteId === null) return;
    setBusy(true); setNotice(null);
    try {
      const response = await fetch(`/auth/${connector}`, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ site_id: siteId, ...body }), signal: AbortSignal.timeout(18000) });
      const result = await response.json() as Record<string, unknown>;
      if (response.status !== 200) throw new Error();
      if (connector !== "github" && body.operation === "authorize") {
        const url = (connector === "bing" ? bingAuthorizationUrl : gscAuthorizationUrl)(result.authorization_url, window.location.origin);
        if (url === null) throw new Error();
        window.location.assign(url);
      } else if (body.operation === "inspect") {
        setNotice(result.state === "observed" ? "Repository access verified." : "Repository access was not verified.");
      } else { window.location.reload(); }
    } catch { setNotice(body.operation === "accept_unprotected" ? "Acceptance was not confirmed. Sign in with fresh MFA and refresh status before retrying." : "Connection was not confirmed. Refresh status before retrying."); }
    finally { setBusy(false); }
  }
  return { busy, notice, command };
}

export function BingConnector({ state, siteId, owner }: { state: BingState; siteId: string | null; owner: boolean }) {
  const { busy, notice, command } = useConnector("bing", siteId);
  const binding = state.availability === "bound" || state.availability === "reauth_required" ? state : null;
  const status = state.availability === "bound" ? "Connected" : state.availability === "reauth_required" ? "Reconnect required" :
    state.availability === "selecting" ? "Confirmation required" : state.availability === "unbound" ? "Not connected" : "Unavailable";
  return <section className="slack-connector" aria-labelledby="bing-title">
    <div className="slack-heading"><h2 id="bing-title"><Search size={18} aria-hidden="true" /> Bing Webmaster Tools</h2><ConnectionStatus>{status}</ConnectionStatus></div>
    {state.availability === "unavailable" ? <p className="muted">Bing is unavailable for this session.</p> :
      !owner || siteId === null ? <p className="muted">An owner must connect Bing.</p> : <>
        <p>Read-only site and top-page search data, kept separate from Google.</p>
        {binding === null ? null : <details className="technical-details"><summary>Technical details</summary><p>Site: {binding.site_url}</p><p>Binding: <code>{binding.binding_id}</code></p></details>}
        <div className="slack-actions">
          {state.availability === "selecting" ? state.sites.map(site => <button className="button button-primary" disabled={busy} key={site.url}
            onClick={() => void command({ operation: "confirm", attempt_id: state.attempt_id, site_url: site.url })}>
            {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Check size={16} aria-hidden="true" />} Confirm verified site</button>) :
            state.availability === "bound" ? null : <button className="button button-primary" disabled={busy}
              onClick={() => void command({ operation: "authorize" })}><Link size={16} aria-hidden="true" /> {binding === null ? "Connect Bing" : "Reconnect Bing"}</button>}
          {binding === null ? null : <button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id })}><Ban size={16} aria-hidden="true" /> Disconnect</button>}
        </div>
      </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}

export function GscConnector({ state, siteId, owner }: { state: GscState; siteId: string | null; owner: boolean }) {
  const { busy, notice, command } = useConnector("gsc", siteId);
  const binding = state.availability === "bound" || state.availability === "reauth_required" ? state : null;
  const status = state.availability === "bound" ? "Connected" : state.availability === "reauth_required" ? "Reconnect required" :
    state.availability === "selecting" ? "Confirmation required" : state.availability === "unbound" ? "Not connected" : "Unavailable";
  return <section className="slack-connector" aria-labelledby="gsc-title">
    <div className="slack-heading"><h2 id="gsc-title"><Search size={18} aria-hidden="true" /> Google Search Console</h2><ConnectionStatus>{status}</ConnectionStatus></div>
    {state.availability === "unavailable" ? <p className="muted">Search Console is unavailable for this session.</p> :
      !owner || siteId === null ? <p className="muted">An owner must connect Search Console.</p> : <>
        {binding !== null ? <dl className="slack-binding"><div><dt>Property</dt><dd>{binding.property_resource_name}</dd></div><div><dt>Access</dt><dd>Read only</dd></div></dl> : null}
        <div className="slack-actions">
          {state.availability === "selecting" ? state.properties.map(property => <button className="button button-primary" disabled={busy} key={property.resource_name}
            onClick={() => void command({ operation: "confirm", attempt_id: state.attempt_id, property_resource_name: property.resource_name })}>
            {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Check size={16} aria-hidden="true" />} Confirm {property.resource_name}</button>) :
            state.availability === "bound" ? null : <button className="button button-primary" disabled={busy}
              onClick={() => void command({ operation: "authorize" })}><Link size={16} aria-hidden="true" /> {binding === null ? "Connect Search Console" : "Reconnect Search Console"}</button>}
          {binding === null ? null : <button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id })}><Ban size={16} aria-hidden="true" /> Disconnect</button>}
        </div>
      </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}

export function GithubConnector({ state, prState = { availability: "unavailable" }, siteId, owner }: { state: GithubState; prState?: GithubPrState; siteId: string | null; owner: boolean }) {
  const { busy, notice, command } = useConnector("github", siteId);
  const [acceptRisk, setAcceptRisk] = useState(false);
  const binding = "binding_id" in state ? state : null;
  const status = state.availability === "active" ? "Connected" : state.availability === "prepared" ? "Verification incomplete" :
    state.availability === "failed" ? "Verification failed" : state.availability === "stale" ? "Reconnect required" :
    state.availability === "unbound" ? "Not connected" : "Unavailable";
  return <section className="slack-connector" aria-labelledby="github-title">
    <div className="slack-heading"><h2 id="github-title"><GitBranch size={18} aria-hidden="true" /> GitHub</h2><ConnectionStatus>{status}</ConnectionStatus></div>
    {binding?.base_protection === "owner_accepted_unprotected" ? <p role="status"><ShieldAlert size={18} aria-hidden="true" /> Unprotected default branch (owner-accepted). Every PR requires owner Inbox approval.</p> : null}
    {state.availability === "unavailable" ? <p className="muted">GitHub is unavailable for this session.</p> :
      !owner || siteId === null ? <p className="muted">An owner must connect GitHub.</p> : <>
        <dl className="slack-binding"><div><dt>Repository</dt><dd>{binding === null ? "Not connected" : `${binding.owner}/${binding.repository}`}</dd></div>
          <div><dt>Branch</dt><dd>{binding?.base_branch ?? "Not selected"}</dd></div><div><dt>Access</dt><dd>Read only</dd></div></dl>
        {binding?.failure_code ? <p role="status">Repository verification failed: {readableCode(binding.failure_code).toLowerCase()}.</p> : null}
        {binding?.base_protection === "unprotected_not_accepted" && (state.availability === "failed" || state.availability === "active") ?
          <div className="slack-actions">
            <label><input type="checkbox" checked={acceptRisk} disabled={busy} onChange={event => setAcceptRisk(event.target.checked)} /> I accept the risk of an unprotected default branch for {binding.owner}/{binding.repository}, branch {binding.base_branch}.</label>
            <button className="button" disabled={busy || !acceptRisk} onClick={() => void command({ operation: "accept_unprotected", binding_id: binding.binding_id, accept_default_branch_unprotected: true })}>
              <ShieldAlert size={16} aria-hidden="true" /> Accept unprotected default branch</button>
            <p className="muted">Owner approval is required for every PR. Sign in with MFA within five minutes before accepting.</p>
          </div> : null}
        <div className="slack-actions">
          {state.availability === "unbound" || state.availability === "failed" ? <button className="button button-primary" disabled={busy}
            onClick={() => void command({ operation: "bind", idempotency_key: crypto.randomUUID() })}>
            {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Link size={16} aria-hidden="true" />} Connect repository</button> : null}
          {state.availability === "active" ? <button className="button" disabled={busy} onClick={() => void command({ operation: "inspect", binding_id: state.binding_id })}><RefreshCw size={16} aria-hidden="true" /> Verify access</button> : null}
          {binding === null ? null : <button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id })}><Ban size={16} aria-hidden="true" /> Disconnect</button>}
        </div>
        {state.availability === "active" ? <GithubPrGrant key={state.binding_id} state={prState} binding={state} siteId={siteId} /> : null}
      </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}

function GithubPrGrant({ state, binding, siteId }: { state: GithubPrState; binding: Extract<GithubState, { binding_id: string }>; siteId: string }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [current, setCurrent] = useState(state);
  const granted = current.availability === "observed";
  const extension = "extension_id" in current ? current : null;
  async function send(body: Record<string, unknown>) {
    const response = await fetch("/auth/github-pr", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ site_id: siteId, ...body }), signal: AbortSignal.timeout(18000) });
    const result = await response.json() as Record<string, unknown>;
    if (response.status !== 200) throw new Error();
    return result;
  }
  async function grant() {
    if (busy) return;
    setBusy(true); setNotice(null);
    try {
      const key = current.availability === "prepared" ? current.idempotency_key : crypto.randomUUID();
      const prepared = current.availability === "prepared" ? { extension_id: current.extension_id, state: "prepared" }
        : await send({ operation: "prepare", binding_id: binding.binding_id, idempotency_key: key });
      if (prepared.state !== "prepared" || typeof prepared.extension_id !== "string") throw new Error();
      const finished = await send({ operation: "finish", binding_id: binding.binding_id, extension_id: prepared.extension_id, idempotency_key: key });
      if (finished.state !== "observed" || finished.extension_id !== prepared.extension_id) throw new Error();
      window.location.reload();
    } catch { setCurrent({ availability: "unavailable" }); setNotice("Permission was not confirmed. Sign in with fresh MFA and refresh to check before retrying."); }
    finally { setBusy(false); }
  }
  async function revoke() {
    if (busy || extension === null) return;
    setBusy(true); setNotice(null);
    try {
      const result = await send({ operation: "revoke", extension_id: extension.extension_id });
      if (result.state !== "revoked" || !["ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"].includes(String(result.durability))) throw new Error();
      setCurrent({ availability: "ungranted" });
      setNotice(result.durability === "AUTHORITY_DURABILITY_PENDING" ? "Permission is revoked locally. Recovery protection is still pending; refresh before relying on it." : "Pull request permission revoked. Read access is unchanged.");
    } catch { setCurrent({ availability: "unavailable" }); setNotice("Revocation was not confirmed. Refresh to check before retrying."); }
    finally { setBusy(false); }
  }
  return <div className="github-pr-grant">
    <h3>Let Signal open pull requests</h3>
    <ConnectionStatus>{granted ? "Granted" : current.availability === "unavailable" ? "Could not check" : current.availability === "prepared" ? "Confirmation incomplete" : current.availability === "failed" || current.availability === "stale" ? "Needs attention" : "Not granted"}</ConnectionStatus>
    <p>Permission to open reviewed pull requests on Signal branches, targeting the selected branch. It never allows merging, pushing to the default branch, editing CI workflows or reading secrets.</p>
    <details className="technical-details"><summary>Technical details</summary>
      <dl className="slack-binding"><div><dt>Repository</dt><dd>{binding.owner}/{binding.repository}</dd></div>
        <div><dt>Base branch</dt><dd>{binding.base_branch}</dd></div><div><dt>Content path</dt><dd>{binding.content_path}</dd></div>
        <div><dt>Write branch scope</dt><dd><code>signal/&lt;32-character operation ID&gt;</code> only</dd></div>
        <div><dt>Read binding</dt><dd>{binding.binding_id}</dd></div>
        {extension ? <div><dt>Permission</dt><dd>{extension.extension_id}</dd></div> : null}
      </dl>
    </details>
    <div className="slack-actions">
      {!granted && current.availability !== "unavailable" && current.availability !== "stale" ? <button className="primary-command" disabled={busy} onClick={() => void grant()}><GitBranch size={16} aria-hidden="true" />{current.availability === "prepared" ? "Finish granting permission" : "Let Signal open pull requests"}</button> : null}
      {extension ? <button className="button button-danger" disabled={busy} onClick={() => void revoke()}><Ban size={16} aria-hidden="true" />Revoke</button> : null}
    </div>
    {!granted && current.availability !== "unavailable" ? <p className="muted">Sign in with MFA within five minutes before granting permission.</p> : null}
    {notice === null ? null : <p role="status">{notice}</p>}
  </div>;
}
