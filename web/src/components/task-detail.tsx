"use client"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { formatDate } from "@/lib/utils"
import type { AgentTask } from "@/lib/types"

interface TaskDetailProps {
  task: AgentTask
  onClose: () => void
}

export function TaskDetail({ task, onClose }: TaskDetailProps) {
  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>{task.trigger}</CardTitle>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Badge variant="outline">{task.type}</Badge>
          <span>{task.status}</span>
          <span>{formatDate(task.created_at)}</span>
        </div>
      </CardHeader>
      <CardContent>
        {task.summary && <p className="mb-4 text-sm">{task.summary}</p>}

        {task.messages && task.messages.length > 0 && (
          <>
            <Separator className="my-3" />
            <div className="flex flex-col gap-3">
              {task.messages.map((msg, i) => {
                const text =
                  typeof msg.content === "string"
                    ? msg.content
                    : msg.content?.text || JSON.stringify(msg.content)
                return (
                  <div key={i} className="text-sm">
                    <span className="font-medium">{msg.role}:</span> {text}
                  </div>
                )
              })}
            </div>
          </>
        )}

        <Button variant="outline" className="mt-4" onClick={onClose}>
          Close
        </Button>
      </CardContent>
    </Card>
  )
}
