"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchSkills } from "@/lib/api"
import type { Skill } from "@/lib/types"

export function useSkills() {
  const queryClient = useQueryClient()
  const query = useQuery<Skill[]>({
    queryKey: ["skills"],
    queryFn: fetchSkills,
  })
  return {
    ...query,
    mutate: () => queryClient.invalidateQueries({ queryKey: ["skills"] }),
  }
}
