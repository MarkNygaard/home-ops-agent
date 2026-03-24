"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchSettings } from "@/lib/api"
import type { Settings } from "@/lib/types"

export function useSettings() {
  const queryClient = useQueryClient()
  const query = useQuery<Settings>({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  })
  return {
    ...query,
    mutate: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  }
}
