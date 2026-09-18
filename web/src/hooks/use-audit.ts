"use client"

import { useQuery } from "@tanstack/react-query"
import { fetchAudit } from "@/lib/api"

export function useAudit(days: number, outcome: string, source: string) {
  return useQuery({
    queryKey: ["audit", days, outcome, source],
    queryFn: () => fetchAudit({ days, outcome, source }),
    // A write can land at any time from a background worker, and this page is
    // the one you leave open when you want to see that happen.
    refetchInterval: 15000,
  })
}
