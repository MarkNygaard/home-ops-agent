"use client"

import useSWR from "swr"
import { fetchPrompts } from "@/lib/api"
import type { PromptsResponse } from "@/lib/types"

export function usePrompts() {
  return useSWR<PromptsResponse>("/api/prompts", fetchPrompts)
}
