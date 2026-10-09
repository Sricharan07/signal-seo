import { DashboardView } from "@/components/dashboard-view";
import { loadDashboardPage } from "@/lib/dashboard-page";

export const dynamic = "force-dynamic";

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <DashboardView {...await loadDashboardPage(searchParams)} />;
}
