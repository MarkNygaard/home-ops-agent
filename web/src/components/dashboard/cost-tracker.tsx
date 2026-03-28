"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
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

export function CostTracker() {
  const [days, setDays] = useState(30)
  const { data: costs, isLoading } = useCosts(days)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">
          API Costs
        </h2>
        <div className="flex gap-1">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              onClick={() => setDays(opt.days)}
              className={`rounded-md px-2 py-0.5 text-xs transition-colors ${
                days === opt.days
                  ? "bg-accent-orange text-white"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          Loading...
        </p>
      ) : !costs || costs.total_requests === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          No API usage recorded yet.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {/* Total summary */}
          <Card size="sm">
            <CardContent>
              <div className="flex items-baseline justify-between">
                <span className="text-2xl font-semibold">
                  {formatCost(costs.total_cost_usd)}
                </span>
                <span className="text-xs text-muted-foreground">
                  {costs.total_requests} requests &middot;{" "}
                  {formatTokens(costs.total_input_tokens + costs.total_output_tokens)} tokens
                </span>
              </div>
            </CardContent>
          </Card>

          {/* Per-model breakdown */}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {costs.by_model.map((m) => (
              <Card key={m.model} size="sm">
                <CardContent className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">
                      {MODEL_LABELS[m.model] ?? m.model}
                    </span>
                    <span className="text-sm font-semibold">
                      {formatCost(m.cost_usd)}
                    </span>
                  </div>
                  <div className="flex gap-2 text-[0.65rem] text-muted-foreground">
                    <span>{m.requests} req</span>
                    <span>&middot;</span>
                    <span>{formatTokens(m.input_tokens)} in</span>
                    <span>&middot;</span>
                    <span>{formatTokens(m.output_tokens)} out</span>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Per-task breakdown */}
          <div className="flex flex-wrap gap-2">
            {costs.by_task.map((t) => (
              <Badge key={t.task_type} variant="secondary" className="text-xs">
                {TASK_LABELS[t.task_type] ?? t.task_type}: {formatCost(t.cost_usd)}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
