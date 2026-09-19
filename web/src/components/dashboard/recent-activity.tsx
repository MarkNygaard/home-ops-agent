"use client"

import Link from "next/link"
import { Card, CardContent } from "@/components/ui/card"
import { useHistory } from "@/hooks/use-history"
import { cn } from "@/lib/utils"
import { TYPE_LABEL, VERDICT_STYLE, plain, relativeTime, splitTags, titleOf } from "@/lib/activity"

export function RecentActivity() {
  const { data: items } = useHistory("")

  const recent = items?.slice(0, 6) ?? []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">
          Recent Activity
        </h2>
        <Link href="/history" className="text-xs text-muted-foreground hover:text-foreground">
          View all
        </Link>
      </div>

      {recent.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No activity yet.
          </CardContent>
        </Card>
      ) : (
        <Card className="py-0">
          <CardContent className="divide-y divide-border/60 px-4 py-0">
            {recent.map((item) => {
              const { tags, rest } = splitTags(item.summary ?? "")
              const body = plain(rest)
              const title = titleOf(item)
              return (
                <Link
                  key={`${item.type}-${item.id}`}
                  href="/history"
                  className="flex items-baseline gap-3 py-2.5 transition-colors hover:bg-muted/40"
                >
                  <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                    {TYPE_LABEL[item.type] ?? item.type.replace(/_/g, " ")}
                  </span>
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
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
                      <p className="line-clamp-1 text-xs text-muted-foreground">{body}</p>
                    )}
                  </div>
                  {/* Relative, not a timestamp: the dashboard question is "what
                      has happened lately", and "9/19/2026, 12:35:36 AM" was the
                      widest thing on the row while answering it worst. */}
                  <span className="shrink-0 text-xs whitespace-nowrap text-muted-foreground/70">
                    {relativeTime(item.created_at)}
                  </span>
                </Link>
              )
            })}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
