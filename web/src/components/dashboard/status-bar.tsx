"use client"

import { useState, useEffect } from "react"
import useSWR from "swr"
import { Badge } from "@/components/ui/badge"
import { useWs } from "@/providers/websocket-provider"
import { useSettings } from "@/hooks/use-settings"
import { fetchStatus } from "@/lib/api"
import { cn } from "@/lib/utils"

function useCountdown(intervalSeconds: number, lastCheckAt: string | null) {
  const [remaining, setRemaining] = useState<number | null>(null)

  useEffect(() => {
    function calcRemaining() {
      if (!lastCheckAt) return intervalSeconds
      const lastCheck = new Date(lastCheckAt).getTime()
      const now = Date.now()
      const elapsed = Math.floor((now - lastCheck) / 1000)
      const rem = intervalSeconds - elapsed
      return rem > 0 ? rem : 0
    }

    setRemaining(calcRemaining())
    const timer = setInterval(() => {
      setRemaining(calcRemaining())
    }, 1000)
    return () => clearInterval(timer)
  }, [intervalSeconds, lastCheckAt])

  if (remaining === null) return "--:--"
  const mins = Math.floor(remaining / 60)
  const secs = remaining % 60
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

export function StatusBar() {
  const { status } = useWs()
  const { data: settings } = useSettings()
  const { data: statusData } = useSWR("/api/status", fetchStatus, {
    refreshInterval: 30000,
  })

  const agentEnabled = settings?.agent_enabled ?? true
  const prMode = settings?.pr_mode ?? "comment_only"
  const prInterval = settings?.pr_check_interval_seconds ?? 1800
  const lastCheckAt = statusData?.last_pr_check_at ?? null
  const countdown = useCountdown(prInterval, lastCheckAt)

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

      <Badge variant={prMode !== "comment_only" ? "accent" : "outline"}>
        {prMode === "auto_merge_all"
          ? "Fully Autonomous"
          : prMode === "auto_merge_minor"
            ? "Auto-Merge Minor"
            : prMode === "auto_merge"
              ? "Auto-Merge Patch"
              : "Comment Only"}
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
