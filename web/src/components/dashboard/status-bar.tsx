"use client"

import { useState, useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import Link from "next/link"
import { useWs } from "@/providers/websocket-provider"
import { useCosts } from "@/hooks/use-costs"
import { useSettings } from "@/hooks/use-settings"
import { fetchStatus, triggerPrCheck } from "@/lib/api"
import { cn } from "@/lib/utils"

function useCountdown(intervalSeconds: number, lastCheckAt: string | null) {
  const [now, setNow] = useState(Date.now)

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now), 1000)
    return () => clearInterval(timer)
  }, [])

  if (!lastCheckAt) return "--:--"
  const elapsed = Math.floor((now - new Date(lastCheckAt).getTime()) / 1000)
  const remaining = Math.max(intervalSeconds - elapsed, 0)
  const mins = Math.floor(remaining / 60)
  const secs = remaining % 60
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

function CostBadge() {
  const { data: costs } = useCosts(30)
  if (!costs || costs.total_requests === 0) return null

  const total = costs.total_cost_usd
  const label = total < 0.01 ? `$${total.toFixed(4)}` : `$${total.toFixed(2)}`

  return (
    <>
      <span className="ml-auto" />
      <Link
        href="/settings/costs"
        className="text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        30d: <span className="font-mono text-foreground">{label}</span>
      </Link>
    </>
  )
}

export function StatusBar() {
  const { status } = useWs()
  const { data: settings } = useSettings()
  const queryClient = useQueryClient()
  const { data: statusData } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    refetchInterval: 30000,
  })

  const [checking, setChecking] = useState(false)
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
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            disabled={checking}
            onClick={async () => {
              setChecking(true)
              try {
                await triggerPrCheck()
                await queryClient.invalidateQueries({ queryKey: ["status"] })
              } finally {
                setChecking(false)
              }
            }}
          >
            {checking ? "Running..." : "Run now"}
          </Button>
        </>
      )}

      <CostBadge />
    </div>
  )
}
