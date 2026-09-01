"use client"

import { useState } from "react"
import { SiteHeader } from "@/components/site-header"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { useAnalytics } from "@/hooks/use-analytics"
import { MODEL_OPTIONS, TASK_LABELS } from "@/lib/constants"

const PERIOD_OPTIONS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "1 year" },
] as const

function modelLabel(value: string): string {
  return MODEL_OPTIONS.find((m) => m.value === value)?.label ?? value
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatCost(usd: number): string {
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`
}

/** A labelled proportion bar.
 *
 *  Single-series magnitude, so one hue is right — the accent the rest of the
 *  dashboard uses. A categorical palette would be wrong here: these bars encode
 *  "how much", not "which one", and the labels already carry identity.
 *
 *  `failedShare` splits the bar into a completed segment and a failed one. That
 *  second colour is the reserved status token, not a second series hue, and it
 *  is always accompanied by the count in `detail` so a failure is never
 *  signalled by colour alone.
 */
function ShareRow({
  label,
  detail,
  share,
  value,
  failedShare = 0,
  muted = false,
}: {
  label: string
  detail?: string
  share: number
  value: string
  failedShare?: number
  muted?: boolean
}) {
  const width = Math.max(share, 1)
  const failedWidth = failedShare > 0 ? Math.max(failedShare, 1) : 0
  const completedWidth = Math.max(width - failedWidth, 0)

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">{label}</span>
        <span className="font-mono text-sm text-muted-foreground">{value}</span>
      </div>
      {/* gap-0.5 is the 2px surface gap that keeps stacked segments readable. */}
      <div className="flex h-2 gap-0.5 overflow-hidden rounded-full bg-muted">
        {completedWidth > 0 && (
          <div
            className={`h-full rounded-full ${muted ? "bg-accent-orange/70" : "bg-accent-orange"}`}
            style={{ width: `${completedWidth}%` }}
          />
        )}
        {failedWidth > 0 && (
          <div
            className="h-full rounded-full bg-destructive"
            style={{ width: `${failedWidth}%` }}
          />
        )}
      </div>
      {detail && <span className="text-xs text-muted-foreground">{detail}</span>}
    </div>
  )
}

export default function SettingsAnalyticsPage() {
  const [days, setDays] = useState(30)
  const { data, isLoading } = useAnalytics(days)

  const maxModelTokens = Math.max(1, ...(data?.by_model ?? []).map((m) => m.total_tokens))
  const maxTaskTokens = Math.max(1, ...(data?.by_task ?? []).map((t) => t.total_tokens))
  const maxRuns = Math.max(1, ...(data?.runs ?? []).map((r) => r.total))

  return (
    <>
      <SiteHeader title="Analytics" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <div className="flex flex-wrap items-center gap-2">
            {PERIOD_OPTIONS.map((p) => (
              <Badge
                key={p.days}
                variant={days === p.days ? "default" : "secondary"}
                className="cursor-pointer"
                onClick={() => setDays(p.days)}
              >
                {p.label}
              </Badge>
            ))}
          </div>

          {isLoading && (
            <p className="py-8 text-center text-sm text-muted-foreground">Loading…</p>
          )}

          {!isLoading && data && data.total_requests === 0 && data.total_runs === 0 && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No agent activity in the last {data.days} days.
            </p>
          )}

          {!isLoading && data && (data.total_requests > 0 || data.total_runs > 0) && (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Tokens</CardDescription>
                    <CardTitle className="font-mono text-2xl">
                      {formatTokens(data.total_tokens)}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="text-xs text-muted-foreground">
                    {formatTokens(data.total_input_tokens)} in ·{" "}
                    {formatTokens(data.total_output_tokens)} out
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Model calls</CardDescription>
                    <CardTitle className="font-mono text-2xl">
                      {data.total_requests}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="text-xs text-muted-foreground">
                    across {data.by_model.length} model
                    {data.by_model.length === 1 ? "" : "s"}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Runs</CardDescription>
                    <CardTitle className="font-mono text-2xl">{data.total_runs}</CardTitle>
                  </CardHeader>
                  <CardContent className="text-xs text-muted-foreground">
                    {data.total_failed > 0 ? (
                      <span className="text-destructive">
                        {data.total_failed} failed
                      </span>
                    ) : (
                      "none failed"
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* Cost is hidden entirely while every provider is plan-billed.
                  It reappears on its own if a metered one is ever added. */}
              {data.is_billed && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Cost</CardDescription>
                    <CardTitle className="font-mono text-2xl">
                      {formatCost(data.total_cost_usd)}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-3">
                    {data.by_model
                      .filter((m) => m.cost_usd > 0)
                      .map((m) => (
                        <ShareRow
                          key={m.model}
                          label={modelLabel(m.model)}
                          share={(m.cost_usd / data.total_cost_usd) * 100}
                          value={formatCost(m.cost_usd)}
                        />
                      ))}
                  </CardContent>
                </Card>
              )}

              <Separator />

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Tokens by model</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  {data.by_model.map((m) => (
                    <ShareRow
                      key={m.model}
                      label={modelLabel(m.model)}
                      detail={`${m.requests} call${m.requests === 1 ? "" : "s"} · ${formatTokens(m.input_tokens)} in · ${formatTokens(m.output_tokens)} out`}
                      share={(m.total_tokens / maxModelTokens) * 100}
                      value={formatTokens(m.total_tokens)}
                    />
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Tokens by agent</CardTitle>
                  <CardDescription>
                    Which agent is consuming the effort.
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  {data.by_task.map((t) => (
                    <ShareRow
                      key={t.task_type}
                      label={TASK_LABELS[t.task_type] ?? t.task_type}
                      detail={`${t.requests} call${t.requests === 1 ? "" : "s"}`}
                      share={(t.total_tokens / maxTaskTokens) * 100}
                      value={formatTokens(t.total_tokens)}
                      muted
                    />
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Runs by type</CardTitle>
                  <CardDescription>
                    Counted from the task log, so failures are visible here and
                    nowhere else. A type with many runs and no successes is worth
                    looking into.
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  {data.runs.map((r) => {
                    const share = (r.total / maxRuns) * 100
                    return (
                      <ShareRow
                        key={r.task_type}
                        label={TASK_LABELS[r.task_type] ?? r.task_type}
                        detail={
                          r.failed > 0
                            ? `${r.completed} completed · ${r.failed} failed`
                            : `${r.completed} completed`
                        }
                        share={share}
                        failedShare={r.total > 0 ? (r.failed / r.total) * share : 0}
                        value={String(r.total)}
                        muted
                      />
                    )
                  })}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  )
}
