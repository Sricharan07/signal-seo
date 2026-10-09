"use client";

import { Ban, Link, LoaderCircle, Send } from "lucide-react";
import { useState } from "react";
import { telegramPairingUrl, type TelegramState } from "../lib/telegram-api";
import { ConnectionStatus, approvalLimit } from "./connection-status";

export function TelegramConnector({ state, siteId, owner }: { state: TelegramState; siteId: string | null; owner: boolean }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [token, setToken] = useState(""), [user, setUser] = useState(""), [risk, setRisk] = useState("2");
  const [pairing, setPairing] = useState<string | null>(null);
  const binding = state.availability === "bound" || state.availability === "failed" ? state : null;
  async function command(body: Record<string, unknown>) {
    if (busy || siteId === null) return;
    setBusy(true); setNotice(null); setPairing(null); setToken("");
    try {
      const response = await fetch("/auth/telegram", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ site_id: siteId, ...body }), signal: AbortSignal.timeout(25000) });
      const result = await response.json() as Record<string, unknown>;
      if (response.status !== 200) { setNotice("Telegram could not confirm the change. Refresh to check setup."); return; }
      if (body.operation === "link") {
        const url = telegramPairingUrl(result.pairing_url);
        if (url === null) throw new Error();
        setPairing(url); setNotice("Pairing expires in five minutes.");
      } else if (body.operation === "revoke") {
        setNotice(result.outcome === "AUTHORITY_DURABILITY_PENDING" ? "Access is blocked. Revocation durability is pending." : "Access revoked.");
        window.location.reload();
      } else { window.location.reload(); }
    } catch { setNotice("Telegram could not confirm the change. Refresh to check setup."); }
    finally { setBusy(false); }
  }
  return <section className="slack-connector" aria-labelledby="telegram-title">
    <div className="slack-heading"><h2 id="telegram-title">Telegram</h2><ConnectionStatus>{state.availability === "bound" ? "Connected" : state.availability === "unbound" ? "Not connected" : state.availability === "failed" ? "Setup not confirmed" : "Unavailable"}</ConnectionStatus></div>
    {state.availability === "unavailable" ? <p className="muted">Telegram is not configured for this deployment.</p> :
      binding === null ? owner && siteId !== null ? <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "install", bot_token: token, max_risk: Number(risk) }); }}>
        <label>Bot token<input type="password" required pattern="[A-Za-z0-9_:-]{16,256}" maxLength={256} value={token} onChange={event => setToken(event.target.value)} autoComplete="off" spellCheck={false} /></label>
        <label>Can approve up to<select value={risk} onChange={event => setRisk(event.target.value)}>{[0, 1, 2].map(level => <option key={level} value={level}>{approvalLimit(level)}</option>)}</select></label>
        <button className="button button-primary" disabled={busy} type="submit">{busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Link size={16} aria-hidden="true" />} Connect Telegram</button>
      </form> : <p className="muted">An owner must connect Telegram.</p> : <>
        {state.availability === "failed" ? <p className="muted">Setup is incomplete. Disconnect before trying again.</p> : <>
          <dl className="slack-binding"><div><dt>Bot</dt><dd>@{binding.bot_username}</dd></div><div><dt>Can approve up to</dt><dd>{approvalLimit(binding.max_risk)}</dd></div></dl>
          {binding.link_id === null ? <form className="slack-form" onSubmit={event => { event.preventDefault(); void command({ operation: "link", binding_id: binding.binding_id, telegram_user_id: user }); }}>
            <label>Your Telegram user ID<input required pattern="[1-9][0-9]{0,15}" maxLength={16} value={user} onChange={event => setUser(event.target.value)} autoComplete="off" inputMode="numeric" /></label>
            <button className="button" disabled={busy} type="submit"><Send size={16} aria-hidden="true" /> Create pairing link</button>
          </form> : <div className="slack-actions"><span>Paired with your Telegram account <code>{binding.telegram_user_id}</code></span><button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id, link_id: binding.link_id })}><Ban size={16} aria-hidden="true" /> Unpair account</button></div>}
        </>}
        {owner ? <button className="button" disabled={busy} onClick={() => void command({ operation: "revoke", binding_id: binding.binding_id })}><Ban size={16} aria-hidden="true" /> Disconnect Telegram</button> : null}
      </>}
    {pairing === null ? null : <a className="button" href={pairing} target="_blank" rel="noopener noreferrer"><Send size={16} aria-hidden="true" /> Pair in Telegram</a>}
    {notice === null ? null : <p role="status">{notice}</p>}
  </section>;
}
