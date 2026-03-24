"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchPrompts } from "@/lib/api"
import type { PromptsResponse } from "@/lib/types"

export function usePrompts() {
  const queryClient = useQueryClient()
  const query = useQuery<PromptsResponse>({
    queryKey: ["prompts"],
    queryFn: fetchPrompts,
  })
  return {
    ...query,
    mutate: () => queryClient.invalidateQueries({ queryKey: ["prompts"] }),
  }
}
