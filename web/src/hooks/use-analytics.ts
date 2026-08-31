"use client"

import { useQuery } from "@tanstack/react-query"
import { fetchAnalytics } from "@/lib/api"
import type { AnalyticsResponse } from "@/lib/types"

export function useAnalytics(days: number = 30) {
  return useQuery<AnalyticsResponse>({
    queryKey: ["analytics", days],
    queryFn: () => fetchAnalytics(days),
    refetchInterval: 60_000, // refresh every minute
  })
}
