"use client"

import useSWR from "swr"
import { fetchConversations, fetchHistory } from "@/lib/api"
import type { HistoryItem } from "@/lib/types"

async function fetchMergedHistory(
  filter: string
): Promise<HistoryItem[]> {
  const [conversations, tasks] = await Promise.all([
    filter && filter !== "chat"
      ? Promise.resolve([])
      : fetchConversations(),
    filter ? fetchHistory(filter) : fetchHistory(),
  ])

  const items: HistoryItem[] = [
    ...conversations.map((c) => ({
      type: "chat",
      id: c.id,
      trigger: c.title,
      created_at: c.created_at,
      summary: null,
      is_conversation: true,
    })),
    ...tasks.map((t) => ({
      type: t.type,
      id: t.id,
      trigger: t.trigger,
      created_at: t.created_at,
      summary: t.summary,
      is_conversation: false,
    })),
  ].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )

  return filter ? items.filter((i) => i.type === filter) : items
}

export function useHistory(filter: string = "") {
  return useSWR<HistoryItem[]>(
    ["history", filter],
    () => fetchMergedHistory(filter)
  )
}
