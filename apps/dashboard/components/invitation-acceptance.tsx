"use client";

import { useEffect, useState } from "react";
import { LogIn, UserPlus } from "lucide-react";
import { invitationCredential } from "@/lib/invitation-route";

export function InvitationAcceptance({ ready, notice }: { ready: boolean; notice: string | null }) {
  const [credential, setCredential] = useState<string | null>(null);
  useEffect(() => {
    const value = invitationCredential(window.location.hash.slice(1));
    if (value !== null) { setCredential(value); window.history.replaceState(null, "", window.location.pathname); }
  }, []);
  const verifying = credential !== null || !ready;
  return <main className="invitation-page">
    <a href="/" className="invitation-brand">Signal</a>
    <h1>Join your team</h1>
    {notice === "denied" ? <p role="alert">This invitation could not be accepted. It may have expired, already been used or been revoked, or your verified account may not match the invited email or organization. Nothing was granted. Ask the owner for a new invitation, or reopen the link and sign in with the invited account.</p> :
      notice === "unconfirmed" ? <p role="alert">We could not confirm acceptance. Check with the owner before retrying. No access is assumed.</p> :
      notice === "invalid-name" ? <p role="alert">Enter your name using 1 to 200 characters.</p> : null}
    {verifying ? <>
      <p>Sign in or create an account with the invited email address. Your identity provider must verify that address.</p>
      {credential === null ? <p>Open the full invitation link shared by your owner to continue.</p> :
        <form action="/auth/invitations/verify" method="post">
          <input type="hidden" name="credential" value={credential} />
          <button className="primary-command"><LogIn size={16} aria-hidden="true" />Sign in or create account</button>
        </form>}
    </> : <form action="/auth/invitations/accept" method="post" className="team-form">
      <p>Your email will be checked against the invitation before any access is granted.</p>
      <label>Your name<input name="display_name" autoComplete="name" required maxLength={200} /></label>
      <button className="primary-command"><UserPlus size={16} aria-hidden="true" />Accept invitation</button>
    </form>}
  </main>;
}
