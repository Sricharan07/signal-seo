import { cookies } from "next/headers";
import { InvitationAcceptance } from "@/components/invitation-acceptance";
import { INVITATION_IDENTITY_COOKIE_NAME } from "@/lib/browser-auth";
import { INVITATION_LINK_COOKIE, invitationCredential } from "@/lib/invitation-route";

export const dynamic = "force-dynamic";
export default async function AcceptPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const store = await cookies();
  const proofs = store.getAll(INVITATION_IDENTITY_COOKIE_NAME), links = store.getAll(INVITATION_LINK_COOKIE);
  const ready = proofs.length === 1 && /^[A-Za-z0-9_-]{43}$/.test(proofs[0].value) && links.length === 1 && invitationCredential(links[0].value) !== null;
  const query = await searchParams;
  return <InvitationAcceptance ready={ready} notice={typeof query.notice === "string" ? query.notice : null} />;
}
