"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchMemories } from "@/lib/api"
import type { Memory } from "@/lib/types"

export function useMemories(category?: string) {
  const queryClient = useQueryClient()
  const query = useQuery<Memory[]>({
    queryKey: ["memories", category],
    queryFn: () => fetchMemories(category),
  })
  return {
    ...query,
    mutate: () => queryClient.invalidateQueries({ queryKey: ["memories"] }),
  }
}
