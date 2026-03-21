"use client"

import useSWR from "swr"
import { fetchMemories } from "@/lib/api"
import type { Memory } from "@/lib/types"

export function useMemories(category?: string) {
  return useSWR<Memory[]>(
    ["/api/memories", category],
    () => fetchMemories(category)
  )
}
