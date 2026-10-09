"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function WorkRefresh({ active }: { active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => router.refresh(), 900);
    return () => window.clearTimeout(timer);
  }, [active, router]);
  return null;
}
