"use client";

import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";

import { api, queryKeys } from "@/lib/api";

/**
 * States plainly when the data on screen is simulated.
 *
 * This exists because the assignment's voice API key expires within
 * days, after which the application serves recorded and simulated calls
 * so the product can still be explored. Showing invented conversations
 * as though they were real screening calls would be dishonest, so the
 * backend reports its effective mode and this renders whatever it says.
 *
 * It fails closed in the useful direction: if `/meta` cannot be reached,
 * nothing is claimed either way rather than asserting the data is live.
 */
export function DemoBanner() {
  const { data } = useQuery({
    queryKey: queryKeys.meta,
    queryFn: api.meta,
    staleTime: 60_000,
    retry: false,
  });

  const banner = data?.voice?.banner;
  if (!banner) return null;

  return (
    <div
      role="status"
      className="border-b border-amber-200/70 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-200"
    >
      <div className="mx-auto flex w-full max-w-[1400px] items-start gap-2.5 px-4 py-2.5 sm:px-6">
        <Info className="mt-0.5 size-4 shrink-0" />
        <p className="text-[13px] leading-relaxed">{banner}</p>
      </div>
    </div>
  );
}
