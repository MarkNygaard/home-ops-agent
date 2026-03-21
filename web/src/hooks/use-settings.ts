"use client"

import useSWR from "swr"
import { fetchSettings } from "@/lib/api"
import type { Settings } from "@/lib/types"

export function useSettings() {
  return useSWR<Settings>("/api/settings", fetchSettings)
}
