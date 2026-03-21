"use client"

import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { useHistory } from "@/hooks/use-history"
import { formatDate } from "@/lib/utils"

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

export function RecentActivity() {
  const { data: items } = useHistory("")

  const recent = items?.slice(0, 5) ?? []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">
          Recent Activity
        </h2>
        <Button variant="link" size="sm" className="text-xs" render={<Link href="/history" />}>
          View all
        </Button>
      </div>

      {recent.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          No activity yet.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {recent.map((item) => (
            <Card key={`${item.type}-${item.id}`} size="sm">
              <CardContent>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <Badge variant={typeBadgeVariant(item.type)}>
                        {item.type.replace("_", " ")}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {formatDate(item.created_at)}
                      </span>
                    </div>
                    <p className="text-sm">{item.trigger}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
