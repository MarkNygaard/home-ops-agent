"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { useHistory } from "@/hooks/use-history"
import { deleteConversation, fetchTaskDetail } from "@/lib/api"
import { formatDate } from "@/lib/utils"
import { useWs } from "@/providers/websocket-provider"
import type { AgentTask, HistoryItem } from "@/lib/types"
import { TaskDetail } from "@/components/task-detail"

const FILTER_TABS = [
  { value: "", label: "All" },
  { value: "pr_review", label: "PRs" },
  { value: "alert_response", label: "Alerts" },
  { value: "chat", label: "Chats" },
  { value: "cluster_fix", label: "Fixes" },
] as const

export default function HistoryPage() {
  const [filter, setFilter] = useState("")
  const { data: items, mutate } = useHistory(filter)
  const { conversationId, setConversationId } = useWs()
  const router = useRouter()
  const [taskDetail, setTaskDetail] = useState<AgentTask | null>(null)

  async function handleDelete(item: HistoryItem) {
    await deleteConversation(item.id)
    if (conversationId === item.id) {
      setConversationId(null)
    }
    mutate()
  }

  async function handleClick(item: HistoryItem) {
    if (item.is_conversation) {
      setConversationId(item.id)
      router.push("/")
    } else {
      const detail = await fetchTaskDetail(item.id)
      setTaskDetail(detail)
    }
  }

  function typeBadgeVariant(type: string) {
    switch (type) {
      case "pr_review":
        return "default" as const
      case "alert_response":
        return "destructive" as const
      case "chat":
        return "secondary" as const
      case "cluster_fix":
        return "outline" as const
      default:
        return "secondary" as const
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-6 py-3">
        <h2 className="text-lg font-semibold">Activity History</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <Tabs
          defaultValue=""
          onValueChange={(val) => setFilter(val as string)}
        >
          <TabsList className="mb-4">
            {FILTER_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {FILTER_TABS.map((tab) => (
            <TabsContent key={tab.value} value={tab.value}>
              {!items || items.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No activity yet.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {items.map((item) => (
                    <Card
                      key={`${item.type}-${item.id}`}
                      className="cursor-pointer transition-colors hover:bg-muted/50"
                      onClick={() => handleClick(item)}
                    >
                      <CardContent>
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex flex-col gap-1.5">
                            <div className="flex items-center gap-2">
                              <Badge variant={typeBadgeVariant(item.type)}>
                                {item.type.replace("_", " ")}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {formatDate(item.created_at)}
                              </span>
                            </div>
                            <p className="text-sm">{item.trigger}</p>
                            {item.summary && (
                              <p className="text-xs text-muted-foreground line-clamp-2">
                                {item.summary}
                              </p>
                            )}
                          </div>
                          {item.is_conversation && (
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDelete(item)
                              }}
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </TabsContent>
          ))}
        </Tabs>

        {taskDetail && (
          <TaskDetail
            task={taskDetail}
            onClose={() => setTaskDetail(null)}
          />
        )}
      </div>
    </div>
  )
}
