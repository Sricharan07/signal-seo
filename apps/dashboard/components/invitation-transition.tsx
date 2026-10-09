import { LogIn, ArrowRight } from "lucide-react";
import { invitationFinishPath } from "@/lib/browser-auth";

export function InvitationTransition({ tenant, site, finish }: { tenant: unknown; site: unknown; finish: boolean }) {
  const valid = typeof tenant === "string" && typeof site === "string" && invitationFinishPath(`/invitations/finish?tenant=${tenant}&site=${site}`);
  return <main className="invitation-page"><a className="invitation-brand" href="/">Signal</a>
    <h1>{finish ? "Open your site" : "Continue to Signal"}</h1>
    {!valid ? <p>The invitation destination is invalid. No site was selected. Ask the owner for help.</p> : <>
      <p>{finish ? "Continue to Home with your invited site selected." : "Sign in with the account that accepted the invitation to open your site."}</p>
      <form action={finish ? "/auth/invitations/finish" : "/auth/invitations/join"} method="post">
        <input type="hidden" name="tenant" value={tenant as string} /><input type="hidden" name="site" value={site as string} />
        <button className="primary-command">{finish ? <ArrowRight size={16} aria-hidden="true" /> : <LogIn size={16} aria-hidden="true" />}{finish ? "Open Home" : "Continue to Signal"}</button>
      </form>
      <details className="technical-details"><summary>Technical details</summary><p>Organization: <code>{tenant as string}</code></p><p>Site: <code>{site as string}</code></p></details>
    </>}
  </main>;
}
