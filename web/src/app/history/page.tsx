"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
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
import { cn, formatDate } from "@/lib/utils"
import {
  TYPE_LABEL,
  VERDICT_STYLE,
  dayLabel,
  plain,
  splitTags,
  timeLabel,
  titleOf,
} from "@/lib/activity"
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

type Group = { key: string; subject: string | null; items: HistoryItem[] }

/** By day, then by the PR consecutive entries share.
 *
 * A review and the merge it led to are one story, and they arrived seconds
 * apart; as two cards they read as two unrelated events.
 */
function group(items: HistoryItem[]): { day: string; groups: Group[] }[] {
  const days: { day: string; groups: Group[] }[] = []

  for (const item of items) {
    const day = dayLabel(item.created_at)
    let bucket = days.at(-1)
    if (!bucket || bucket.day !== day) {
      bucket = { day, groups: [] }
      days.push(bucket)
    }

    const subject = item.trigger.match(/^PR #\d+$/) ? item.trigger : null
    const last = bucket.groups.at(-1)
    if (last && subject !== null && last.subject === subject) {
      last.items.push(item)
    } else {
      bucket.groups.push({ key: `${item.type}-${item.id}`, subject, items: [item] })
    }
  }

  return days
}

export default function HistoryPage() {
  const [filter, setFilter] = useState("")
  const { data: items, mutate } = useHistory(filter)
  const { conversationId, setConversationId } = useWs()
  const router = useRouter()
  const [taskDetail, setTaskDetail] = useState<AgentTask | null>(null)

  async function handleDelete(item: HistoryItem) {
    await deleteConversation(item.id)
    if (conversationId === item.id) setConversationId(null)
    mutate()
  }

  async function handleClick(item: HistoryItem) {
    if (item.is_conversation) {
      setConversationId(item.id)
      router.push("/chat")
    } else {
      setTaskDetail(await fetchTaskDetail(item.id))
    }
  }

  const byDay = group(items ?? [])

  return (
    <>
      <SiteHeader title="Activity History" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-5xl flex-col gap-6">
          <Tabs value={filter} onValueChange={setFilter}>
            <TabsList>
              {FILTER_TABS.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          {!items || items.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                No activity yet.
              </CardContent>
            </Card>
          ) : (
            <div className="flex flex-col gap-6">
              {byDay.map((bucket) => (
                <div key={bucket.day} className="flex flex-col gap-2">
                  <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    {bucket.day}
                  </h2>
                  <Card className="py-0">
                    <CardContent className="divide-y divide-border/60 px-4 py-0">
                      {bucket.groups.map((g) => (
                        <div key={g.key} className="py-2">
                          {g.subject && g.items.length > 1 && (
                            <div className="pb-1 text-xs font-medium text-accent-orange">
                              {g.subject}
                            </div>
                          )}
                          {g.items.map((item) => {
                            const { tags, rest } = splitTags(item.summary ?? "")
                            const body = plain(rest)
                            const title = titleOf(item)
                            return (
                              <div
                                key={`${item.type}-${item.id}`}
                                className="group flex cursor-pointer items-baseline gap-3 py-1.5"
                                onClick={() => handleClick(item)}
                              >
                                <span className="w-12 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                                  {timeLabel(item.created_at)}
                                </span>
                                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                                    <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                                      {TYPE_LABEL[item.type] ?? item.type.replace(/_/g, " ")}
                                    </span>
                                    <span className="truncate text-sm" title={title}>
                                      {title}
                                    </span>
                                    {tags.map((tag) => (
                                      <span
                                        key={tag}
                                        className={cn(
                                          "shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium",
                                          VERDICT_STYLE[tag] ?? "bg-muted text-muted-foreground"
                                        )}
                                      >
                                        {tag.replace(/_/g, " ").toLowerCase()}
                                      </span>
                                    ))}
                                  </div>
                                  {body && (
                                    <p className="line-clamp-1 text-xs text-muted-foreground">
                                      {body}
                                    </p>
                                  )}
                                </div>
                                {item.is_conversation && (
                                  <Button
                                    variant="ghost"
                                    size="icon-xs"
                                    className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      handleDelete(item)
                                    }}
                                  >
                                    <Trash2 className="size-3.5" />
                                  </Button>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                </div>
              ))}
            </div>
          )}
        </div>

        <Dialog
          open={taskDetail !== null}
          onOpenChange={(open: boolean) => {
            if (!open) setTaskDetail(null)
          }}
        >
          <DialogContent className="fixed top-6 right-6 left-auto bottom-auto max-h-[calc(100vh-3rem)] translate-x-0 translate-y-0 overflow-y-auto sm:max-w-lg md:max-w-xl lg:max-w-2xl">
            <DialogHeader>
              <DialogTitle>{taskDetail?.trigger ?? "Task Detail"}</DialogTitle>
            </DialogHeader>
            {taskDetail && (
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="rounded bg-muted px-1.5 py-0.5">
                    {TYPE_LABEL[taskDetail.type] ?? taskDetail.type}
                  </span>
                  <span>{taskDetail.status}</span>
                  <span>{formatDate(taskDetail.created_at)}</span>
                </div>

                {taskDetail.summary && (
                  // Kept as written, with line breaks: the dialog is where you
                  // go for the whole thing, so flattening it here would leave
                  // nowhere to read it properly.
                  <p className="text-sm whitespace-pre-wrap">
                    {splitTags(taskDetail.summary).rest}
                  </p>
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
                            <span className="whitespace-pre-wrap">{text}</span>
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
