"use client";

import { useState } from "react";
import { Ban, X } from "lucide-react";

export function InvitationRevocationConfirmation({ busy, onConfirm, onCancel }: {
  busy: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return <div className="invitation-revoke-confirmation" role="group" aria-label="Confirm invitation revocation">
    <p>Revoke this invitation? The shared link will stop working. Current members keep their access.</p>
    <button type="button" className="danger-command" disabled={busy} onClick={onConfirm}><Ban size={16} aria-hidden="true" />{busy ? "Revoking invitation" : "Revoke invitation"}</button>
    <button type="button" disabled={busy} onClick={onCancel}><X size={16} aria-hidden="true" />Cancel</button>
  </div>;
}

export function InvitationRevocation({ siteId, invitationId, onRevoked }: {
  siteId: string; invitationId: string; onRevoked: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  async function revoke() {
    setBusy(true); setNotice(null);
    try {
      const response = await fetch("/auth/team/revoke", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, invitation_id: invitationId, confirmed: true }) });
      const body = await response.json();
      if (response.status === 200 && body.state === "revoked") { onRevoked(); return; }
      setNotice(body.state === "denied" ? "Revocation denied. Reload team to check its current state, then verify the site and sign in with MFA again." :
        "Revocation could not be confirmed. Reload team before retrying; no change is assumed.");
    } catch { setNotice("Revocation could not be confirmed. Reload team before retrying; no change is assumed."); }
    finally { setBusy(false); setConfirming(false); }
  }
  return <>
    {confirming ? <InvitationRevocationConfirmation busy={busy} onConfirm={() => void revoke()} onCancel={() => setConfirming(false)} /> :
      <button type="button" className="danger-command" onClick={() => setConfirming(true)}><Ban size={16} aria-hidden="true" />Revoke</button>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </>;
}
