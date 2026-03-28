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
import { useCosts } from "@/hooks/use-costs"

const MODEL_LABELS: Record<string, string> = {
  "claude-haiku-4-5": "Haiku 4.5",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-opus-4-6": "Opus 4.6",
}

const TASK_LABELS: Record<string, string> = {
  pr_review: "PR Review",
  deep_review: "Deep Review",
  alert_triage: "Alert Triage",
  alert_fix: "Alert Fix",
  code_fix: "Code Fix",
  chat: "Chat",
}

const PERIOD_OPTIONS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "1 year" },
] as const

function formatCost(usd: number): string {
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export default function SettingsCostsPage() {
  const [days, setDays] = useState(30)
  const { data: costs, isLoading } = useCosts(days)

  return (
    <>
      <SiteHeader title="API Costs" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Anthropic API usage and costs across all agent tasks.
            </p>
            <div className="flex gap-1 rounded-lg bg-muted p-1">
              {PERIOD_OPTIONS.map((opt) => (
                <button
                  key={opt.days}
                  onClick={() => setDays(opt.days)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                    days === opt.days
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {isLoading ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Loading...
            </p>
          ) : !costs || costs.total_requests === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No API usage recorded yet.
            </p>
          ) : (
            <>
              {/* Summary cards */}
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Card size="sm">
                  <CardContent className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      Total Cost
                    </span>
                    <span className="text-2xl font-semibold">
                      {formatCost(costs.total_cost_usd)}
                    </span>
                  </CardContent>
                </Card>
                <Card size="sm">
                  <CardContent className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      Requests
                    </span>
                    <span className="text-2xl font-semibold">
                      {costs.total_requests.toLocaleString()}
                    </span>
                  </CardContent>
                </Card>
                <Card size="sm">
                  <CardContent className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      Input Tokens
                    </span>
                    <span className="text-2xl font-semibold">
                      {formatTokens(costs.total_input_tokens)}
                    </span>
                  </CardContent>
                </Card>
                <Card size="sm">
                  <CardContent className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      Output Tokens
                    </span>
                    <span className="text-2xl font-semibold">
                      {formatTokens(costs.total_output_tokens)}
                    </span>
                  </CardContent>
                </Card>
              </div>

              <Separator />

              {/* Per-model breakdown */}
              <div className="flex flex-col gap-4">
                <h3 className="text-sm font-medium">Cost by Model</h3>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                  {costs.by_model.map((m) => {
                    const pricing = costs.pricing[m.model]
                    const pctOfTotal =
                      costs.total_cost_usd > 0
                        ? (m.cost_usd / costs.total_cost_usd) * 100
                        : 0

                    return (
                      <Card key={m.model}>
                        <CardHeader>
                          <CardTitle>
                            {MODEL_LABELS[m.model] ?? m.model}
                          </CardTitle>
                          <CardDescription>
                            {pctOfTotal.toFixed(0)}% of total spend
                          </CardDescription>
                        </CardHeader>
                        <CardContent className="flex flex-col gap-3">
                          <div className="flex items-baseline justify-between">
                            <span className="text-3xl font-semibold">
                              {formatCost(m.cost_usd)}
                            </span>
                            <span className="text-sm text-muted-foreground">
                              {m.requests} requests
                            </span>
                          </div>

                          {/* Cost bar */}
                          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full rounded-full bg-accent-orange"
                              style={{ width: `${Math.max(pctOfTotal, 1)}%` }}
                            />
                          </div>

                          <div className="grid grid-cols-2 gap-3 text-sm">
                            <div className="flex flex-col gap-0.5">
                              <span className="text-xs text-muted-foreground">
                                Input
                              </span>
                              <span className="font-mono">
                                {formatTokens(m.input_tokens)}
                              </span>
                              {pricing && (
                                <span className="text-xs text-muted-foreground">
                                  ${pricing.input}/MTok
                                </span>
                              )}
                            </div>
                            <div className="flex flex-col gap-0.5">
                              <span className="text-xs text-muted-foreground">
                                Output
                              </span>
                              <span className="font-mono">
                                {formatTokens(m.output_tokens)}
                              </span>
                              {pricing && (
                                <span className="text-xs text-muted-foreground">
                                  ${pricing.output}/MTok
                                </span>
                              )}
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    )
                  })}
                </div>
              </div>

              <Separator />

              {/* Per-task breakdown */}
              <div className="flex flex-col gap-4">
                <h3 className="text-sm font-medium">Cost by Task Type</h3>
                <div className="flex flex-col gap-2">
                  {costs.by_task
                    .sort((a, b) => b.cost_usd - a.cost_usd)
                    .map((t) => {
                      const pctOfTotal =
                        costs.total_cost_usd > 0
                          ? (t.cost_usd / costs.total_cost_usd) * 100
                          : 0

                      return (
                        <div
                          key={t.task_type}
                          className="flex items-center gap-4 rounded-lg bg-card px-4 py-3 ring-1 ring-foreground/10"
                        >
                          <Badge variant="secondary" className="min-w-[100px] justify-center">
                            {TASK_LABELS[t.task_type] ?? t.task_type}
                          </Badge>
                          <div className="flex-1">
                            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-accent-orange/70"
                                style={{
                                  width: `${Math.max(pctOfTotal, 1)}%`,
                                }}
                              />
                            </div>
                          </div>
                          <span className="min-w-[50px] text-right text-sm font-mono">
                            {formatCost(t.cost_usd)}
                          </span>
                          <span className="min-w-[60px] text-right text-xs text-muted-foreground">
                            {t.requests} req
                          </span>
                        </div>
                      )
                    })}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  )
}
