"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchConversations, fetchHistory } from "@/lib/api"
import type { HistoryItem } from "@/lib/types"

async function fetchMergedHistory(
  filter: string
): Promise<HistoryItem[]> {
  const [conversations, tasks] = await Promise.all([
    fetchConversations(),
    fetchHistory(),
  ])

  const items: HistoryItem[] = [
    // Only include user-initiated chat conversations (not PR review or alert conversations)
    ...conversations
      .filter((c) => c.source === "chat")
      .map((c) => ({
        type: "chat" as const,
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
  const queryClient = useQueryClient()
  const query = useQuery<HistoryItem[]>({
    queryKey: ["history", filter],
    queryFn: () => fetchMergedHistory(filter),
  })
  return {
    ...query,
    mutate: () => queryClient.invalidateQueries({ queryKey: ["history"] }),
  }
}
