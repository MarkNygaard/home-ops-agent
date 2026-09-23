"use client"

import { useState, useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import Link from "next/link"
import { useWs } from "@/providers/websocket-provider"
import { useAnalytics } from "@/hooks/use-analytics"
import { useSettings } from "@/hooks/use-settings"
import { fetchStatus, triggerPrCheck } from "@/lib/api"
import type { PrCheckResult } from "@/lib/types"
import { cn, formatAgo, msSince } from "@/lib/utils"

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

// A PR check that reviews nothing is indistinguishable from one that never ran
// unless the reason is spelled out. Every branch of check_prs reports itself.
function describeCheck(result: PrCheckResult | null | undefined): string | null {
  if (!result) return null
  switch (result.status) {
    case "disabled":
      return "Agent is disabled"
    case "no_credentials":
      return "No model credentials"
    case "no_open_prs":
      return "No open PRs"
    case "cancelled":
      return "Check cancelled"
    case "error":
      return `Check failed: ${result.error ?? "unknown error"}`
    case "completed": {
      const parts = [`${result.reviewed ?? 0} reviewed`]
      if (result.merged) parts.push(`${result.merged} merged`)
      // Skipped PRs were already reviewed at this head. Shown because the
      // count used to be filed under "failed", which made a healthy cycle
      // read as a broken one.
      if (result.skipped) parts.push(`${result.skipped} already reviewed`)
      if (result.failed) parts.push(`${result.failed} failed`)
      if (result.rate_limited) parts.push("rate limited")
      return parts.join(", ")
    }
    default:
      return result.status
  }
}

function UsageBadge() {
  const { data } = useAnalytics(30)
  if (!data || data.total_requests === 0) return null

  // Show spend when there is any, otherwise token volume: a permanent "$0.00"
  // is noise, while tokens say something whichever way billing works.
  const label = data.is_billed
    ? data.total_cost_usd < 0.01
      ? `$${data.total_cost_usd.toFixed(4)}`
      : `$${data.total_cost_usd.toFixed(2)}`
    : `${(data.total_tokens / 1_000_000).toFixed(1)}M tok`

  return (
    <Link
      href="/settings/analytics"
      className="text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      30d: <span className="font-mono text-foreground">{label}</span>
    </Link>
  )
}

// How long a restart stays newsworthy. Long enough that a blip is still on the
// bar when you next glance at it, short enough that it does not become part of
// the furniture. Past this the notice greys out rather than disappearing.
const RECENT_RESTART_MS = 60 * 60 * 1000

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
  // Set only for outcomes the trigger reports directly (already running,
  // failed to start); otherwise the last cycle's result is shown.
  const [lastRun, setLastRun] = useState<string | null>(null)
  const agentEnabled = settings?.agent_enabled ?? true
  const prMode = settings?.pr_mode ?? "comment_only"
  const prInterval = settings?.pr_check_interval_seconds ?? 1800
  const lastCheckAt = statusData?.last_pr_check_at ?? null
  // A dead background worker leaves the HTTP server answering, so nothing else
  // on this page would look any different. It is worth a line of its own.
  //
  // Severity is by recency, not by the lifetime restart count. `restarts` never
  // decays, so a worker that blipped three times in 35 seconds during a
  // database rollout kept a red dot on this bar for the next three days. A
  // permanent alarm for a resolved incident is one you learn to ignore, which
  // costs exactly the thing this line exists to buy.
  const workers = statusData?.workers ?? []
  const down = workers.filter((w) => !w.alive)
  const restarted = workers.filter((w) => w.alive && w.restarts > 0)
  const recent = restarted.filter((w) => msSince(w.last_death_at) < RECENT_RESTART_MS)
  const settled = restarted.filter((w) => msSince(w.last_death_at) >= RECENT_RESTART_MS)

  // Down outranks restarted: a worker that is not coming back is the headline
  // even if another one merely stumbled a minute ago.
  const notice =
    down.length > 0
      ? {
          dot: "bg-red-500",
          text: down.length === 1 ? `Worker down: ${down[0].name}` : `${down.length} workers down`,
          workers: [...down, ...restarted],
        }
      : recent.length > 0
        ? {
            dot: "bg-amber-500",
            text:
              recent.length === 1
                ? `Worker restarted: ${recent[0].name}`
                : `${recent.length} workers restarted`,
            workers: restarted,
          }
        : settled.length > 0
          ? {
              // Still shown, because this bar is the only place workers surface
              // at all — but grey and dated, as history rather than an alarm.
              dot: "bg-muted-foreground",
              text:
                settled.length === 1
                  ? `Worker restarted: ${settled[0].name} · ${formatAgo(settled[0].last_death_at)}`
                  : `${settled.length} workers restarted earlier`,
              workers: settled,
            }
          : null

  const noticeDetail = notice?.workers
    .map(
      (w) =>
        `${w.name}: ${w.last_error ?? "restarted"} (${w.restarts}× restarted, last ${formatAgo(w.last_death_at)})`
    )
    .join(" | ")
  const countdown = useCountdown(prInterval, lastCheckAt)

  return (
    <div className="flex items-center justify-between gap-3 rounded-xl bg-card px-5 py-3.5 ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-center gap-3">
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

        {notice && (
          <>
            <span className="text-border">|</span>
            <div className="flex items-center gap-2" title={noticeDetail}>
              <span className={cn("size-2 rounded-full", notice.dot)} />
              <span className="text-sm text-muted-foreground">{notice.text}</span>
            </div>
          </>
        )}

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
              disabled={checking || statusData?.pr_check_running}
              onClick={async () => {
                setChecking(true)
                setLastRun(null)
                try {
                  const res = await triggerPrCheck()
                  if (res.status === "already_running") {
                    setLastRun("Already running")
                  }
                  await queryClient.invalidateQueries({ queryKey: ["status"] })
                } catch {
                  setLastRun("Could not start check")
                } finally {
                  setChecking(false)
                }
              }}
            >
              {checking || statusData?.pr_check_running ? "Running..." : "Run now"}
            </Button>
            {(lastRun ?? describeCheck(statusData?.last_pr_check_result)) && (
              <span className="text-xs text-muted-foreground">
                {lastRun ?? describeCheck(statusData?.last_pr_check_result)}
              </span>
            )}
          </>
        )}
      </div>

      <UsageBadge />
    </div>
  )
}
