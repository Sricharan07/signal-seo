import { InvitationTransition } from "@/components/invitation-transition";
export const dynamic = "force-dynamic";
export default async function FinishPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  return <InvitationTransition tenant={query.tenant} site={query.site} finish />;
}
