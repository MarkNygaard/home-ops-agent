"use client"

import { useState, useEffect } from "react"
import { Badge } from "@/components/ui/badge"
import { useWs } from "@/providers/websocket-provider"
import { useSettings } from "@/hooks/use-settings"
import { cn } from "@/lib/utils"

function useCountdown(intervalSeconds: number) {
  const [remaining, setRemaining] = useState(intervalSeconds)

  useEffect(() => {
    setRemaining(intervalSeconds)
    const timer = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) return intervalSeconds
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [intervalSeconds])

  const mins = Math.floor(remaining / 60)
  const secs = remaining % 60
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

export function StatusBar() {
  const { status } = useWs()
  const { data: settings } = useSettings()

  const agentEnabled = settings?.agent_enabled ?? true
  const prMode = settings?.pr_mode ?? "comment_only"
  const prInterval = settings?.pr_check_interval_seconds ?? 1800
  const countdown = useCountdown(prInterval)

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl bg-card px-5 py-3.5 ring-1 ring-foreground/10">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-2.5 rounded-full",
            agentEnabled ? "bg-green-500" : "bg-muted-foreground"
          )}
        />
        <span className="text-sm font-medium">
          Agent {agentEnabled ? "Enabled" : "Disabled"}
        </span>
      </div>

      <span className="text-border">|</span>

      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-2 rounded-full",
            status === "connected" && "bg-green-500",
            status === "connecting" && "bg-yellow-500",
            status === "disconnected" && "bg-muted-foreground"
          )}
        />
        <span className="text-sm text-muted-foreground">
          {status === "connected"
            ? "Connected"
            : status === "connecting"
              ? "Connecting..."
              : "Disconnected"}
        </span>
      </div>

      <span className="text-border">|</span>

      <Badge variant={prMode === "auto_merge" ? "accent" : "outline"}>
        {prMode === "auto_merge" ? "Auto-Merge" : "Comment Only"}
      </Badge>

      {agentEnabled && (
        <>
          <span className="text-border">|</span>
          <span className="text-sm text-muted-foreground">
            Next PR check in{" "}
            <span className="font-mono text-foreground">{countdown}</span>
          </span>
        </>
      )}
    </div>
  )
}
