"use client"

import { useQuery } from "@tanstack/react-query"
import { fetchCosts } from "@/lib/api"
import type { CostsResponse } from "@/lib/types"

export function useCosts(days: number = 30) {
  return useQuery<CostsResponse>({
    queryKey: ["costs", days],
    queryFn: () => fetchCosts(days),
    refetchInterval: 60_000, // refresh every minute
  })
}
