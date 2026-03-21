"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Separator } from "@/components/ui/separator"
import { SiteHeader } from "@/components/site-header"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useHistory } from "@/hooks/use-history"
import { deleteConversation, fetchTaskDetail } from "@/lib/api"
import { formatDate } from "@/lib/utils"
import { useWs } from "@/providers/websocket-provider"
import type { AgentTask, HistoryItem } from "@/lib/types"

const FILTER_TABS = [
  { value: "", label: "All" },
  { value: "pr_review", label: "PRs" },
  { value: "pr_merge", label: "Merges" },
  { value: "alert_triage", label: "Triage" },
  { value: "alert_fix", label: "Fixes" },
  { value: "code_fix", label: "Code Fixes" },
  { value: "chat", label: "Chats" },
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
      router.push("/chat")
    } else {
      const detail = await fetchTaskDetail(item.id)
      setTaskDetail(detail)
    }
  }

  function typeBadgeVariant(type: string) {
    switch (type) {
      case "pr_review":
        return "default" as const
      case "pr_merge":
        return "default" as const
      case "alert_response":
      case "alert_triage":
      case "alert_fix":
        return "destructive" as const
      case "code_fix":
      case "cluster_fix":
        return "outline" as const
      case "chat":
        return "secondary" as const
      default:
        return "secondary" as const
    }
  }

  return (
    <>
      <SiteHeader title="Activity History" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto max-w-6xl">
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
        </div>

        <Dialog
          open={taskDetail !== null}
          onOpenChange={(open: boolean) => {
            if (!open) setTaskDetail(null)
          }}
        >
          <DialogContent className="fixed top-6 right-6 left-auto bottom-auto translate-x-0 translate-y-0 sm:max-w-lg md:max-w-xl lg:max-w-2xl max-h-[calc(100vh-3rem)] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{taskDetail?.trigger ?? "Task Detail"}</DialogTitle>
            </DialogHeader>
            {taskDetail && (
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Badge variant="outline">{taskDetail.type}</Badge>
                  <span>{taskDetail.status}</span>
                  <span>{formatDate(taskDetail.created_at)}</span>
                </div>

                {taskDetail.summary && (
                  <p className="text-sm">{taskDetail.summary}</p>
                )}

                {taskDetail.messages && taskDetail.messages.length > 0 && (
                  <>
                    <Separator />
                    <div className="flex flex-col gap-3">
                      {taskDetail.messages.map((msg, i) => {
                        const text =
                          typeof msg.content === "string"
                            ? msg.content
                            : msg.content?.text || JSON.stringify(msg.content)
                        return (
                          <div key={i} className="text-sm">
                            <span className="font-medium">{msg.role}:</span>{" "}
                            {text}
                          </div>
                        )
                      })}
                    </div>
                  </>
                )}
              </div>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </>
  )
}
