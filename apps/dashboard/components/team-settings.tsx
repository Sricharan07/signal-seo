"use client";

import { readableCode } from "./home-panels";
import { useState } from "react";
import { Copy, RefreshCw, UserPlus } from "lucide-react";
import { INVITE_ROLES, type TeamState } from "@/lib/team-api";
import { InvitationRevocation } from "./invitation-revocation";

export function TeamSettings({ state, siteId }: { state: TeamState; siteId: string }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [revoked, setRevoked] = useState<Set<string>>(new Set());
  async function invite(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setLink(null); setNotice(null);
    try {
      const response = await fetch("/auth/team", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, email: form.get("email"), role_key: form.get("role_key") }) });
      const body = await response.json();
      if (response.status === 201 && body.state === "created" && typeof body.link === "string") {
        setLink(body.link); setNotice(`Invitation created. It was not emailed. It expires ${new Date(body.expiresAt).toLocaleString()}.`);
      } else setNotice(body.state === "denied" ? "Invitation denied. Verify the site and sign in with MFA again before retrying." :
        body.state === "pending" ? "A pending invitation already exists for this address on this site." :
        "Invitation creation could not be confirmed. Check the invitation list before retrying; the link cannot be recovered.");
    } catch { setNotice("Invitation creation could not be confirmed. Check the invitation list before retrying."); }
    finally { setBusy(false); }
  }
  return <section className="settings-section" aria-labelledby="team-title">
    <h2 id="team-title">Team</h2>
    <button type="button" disabled={busy || link !== null} onClick={() => window.location.reload()}><RefreshCw size={16} aria-hidden="true" />Reload team</button>
    {state.state === "unavailable" ? <p>Team information could not be loaded. No invitation controls are available.</p> : <>
      <h3>Members</h3>
      <ul className="team-list">{state.members.map(member => <li key={member.user_id}>
        <span>{member.display_name}</span><span className="status-pill">{member.role_key}</span>
        <details className="technical-details"><summary>Technical details</summary><code>{member.user_id}</code></details>
      </li>)}</ul>
      <h3>Invite someone</h3>
      <form onSubmit={invite} className="team-form">
        <label>Email address<input name="email" type="email" required maxLength={320} autoComplete="off" disabled={busy} /></label>
        <label>Role<select name="role_key" defaultValue="viewer" disabled={busy}>
          {INVITE_ROLES.map(role => <option key={role} value={role}>{role[0].toUpperCase() + role.slice(1)}</option>)}
        </select></label>
        <button className="primary-command" disabled={busy}><UserPlus size={16} aria-hidden="true" />{busy ? "Creating invitation" : "Create invitation"}</button>
      </form>
      {link === null ? null : <div className="invitation-share">
        <label>One-time share link<input readOnly value={link} onFocus={event => event.currentTarget.select()} /></label>
        <button type="button" title="Copy invitation link" aria-label="Copy invitation link" onClick={() => void navigator.clipboard.writeText(link).then(
          () => setNotice("Invitation link copied. It was not emailed."), () => setNotice("The link could not be copied. Select the share link instead."))}><Copy size={16} /></button>
        <button type="button" onClick={() => { setLink(null); window.location.reload(); }}>Done</button>
      </div>}
      <h3>Invitations</h3>
      {state.invitations.length === 0 ? <p>No invitations yet.</p> : <ul className="team-list">{state.invitations.map(original => {
        const invitation = revoked.has(original.id) ? { ...original, state: "revoked", revocation_durability: "pending" } : original;
        return <li key={invitation.id}>
        <span>{invitation.email}</span><span>{invitation.role_key}</span><span className="status-pill">{invitation.state === "revoked" ? "Revoked locally" : readableCode(invitation.state)}</span>
        <span>Expires {new Date(invitation.expires_at).toLocaleDateString()}</span>
        {invitation.state === "revoked" ? <span>{invitation.revocation_durability === "acknowledged" ? "Recovery confirmation recorded" : "Recovery confirmation pending"}</span> : null}
        {state.can_revoke && invitation.state === "pending" ? <InvitationRevocation siteId={siteId} invitationId={invitation.id}
          onRevoked={() => { setRevoked(previous => new Set([...previous, invitation.id])); setNotice("Invitation revoked locally. Recovery confirmation is pending."); }} /> : null}
        <details className="technical-details"><summary>Technical details</summary><code>{invitation.id}</code></details>
      </li>; })}</ul>}
      {state.truncated ? <p>Only the latest 100 invitations and first 100 members are shown.</p> : null}
    </>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}
