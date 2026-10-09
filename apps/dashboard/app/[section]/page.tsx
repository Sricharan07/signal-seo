import { notFound } from "next/navigation";

import {
  DashboardView,
  dashboardSections,
  type DashboardSection,
} from "@/components/dashboard-view";
import { loadDashboardPage } from "@/lib/dashboard-page";

export const dynamic = "force-dynamic";

export default async function ProductSectionPage({
  params,
  searchParams,
}: {
  params: Promise<{ section: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { section } = await params;
  if (!isDashboardSection(section) || section === "overview") notFound();
  return (
    <DashboardView
      {...await loadDashboardPage(searchParams, section)}
      authNotice={null}
      activeSection={section}
    />
  );
}

function isDashboardSection(value: string): value is DashboardSection {
  return dashboardSections.some((section) => section === value);
}
