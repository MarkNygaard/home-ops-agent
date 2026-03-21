"use client"

import useSWR from "swr"
import { fetchSkills } from "@/lib/api"
import type { Skill } from "@/lib/types"

export function useSkills() {
  return useSWR<Skill[]>("/api/skills", fetchSkills)
}
