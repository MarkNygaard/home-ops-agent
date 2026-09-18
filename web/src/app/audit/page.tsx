"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SiteHeader } from "@/components/site-header"
import { useAudit } from "@/hooks/use-audit"
import { formatDate } from "@/lib/utils"
import type { WriteRecord } from "@/lib/types"

const WINDOWS = [
  { value: 1, label: "24 hours" },
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
]

const OUTCOMES = [
  { value: "", label: "All" },
  { value: "ok", label: "Applied" },
  { value: "blocked", label: "Blocked" },
  { value: "error", label: "Failed" },
]

function outcomeVariant(outcome: string) {
  if (outcome === "blocked") return "secondary" as const
  if (outcome === "error") return "destructive" as const
  return "default" as const
}

function outcomeLabel(outcome: string) {
  if (outcome === "blocked") return "blocked"
  if (outcome === "error") return "failed"
  return "applied"
}

export default function AuditPage() {
  const [days, setDays] = useState(7)
  const [outcome, setOutcome] = useState("")
  const [source, setSource] = useState("")
  const { data, isLoading } = useAudit(days, outcome, source)

  const writes: WriteRecord[] = data?.writes ?? []

  return (
    <>
      <SiteHeader title="Write Audit" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <p className="text-sm text-muted-foreground">
            Every change the agent made — restarts, reconciles, commits, pull
            requests, merges — across all runs, newest first. Reads are not
            listed: they are the overwhelming majority of what the agent does,
            and including them would bury the lines that matter.
          </p>

          <div className="flex flex-wrap items-center gap-6">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums">
                {data?.counts.total ?? 0}
              </span>
              <span className="text-sm text-muted-foreground">writes</span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums">
                {data?.counts.blocked ?? 0}
              </span>
              <span className="text-sm text-muted-foreground">blocked by a guardrail</span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums">
                {data?.counts.error ?? 0}
              </span>
              <span className="text-sm text-muted-foreground">failed</span>
            </div>
          </div>

          <Separator />

          <div className="flex flex-wrap items-center gap-3">
            <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v))}>
              <TabsList>
                {WINDOWS.map((w) => (
                  <TabsTrigger key={w.value} value={String(w.value)}>
                    {w.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>

            <Tabs value={outcome} onValueChange={setOutcome}>
              <TabsList>
                {OUTCOMES.map((o) => (
                  <TabsTrigger key={o.value} value={o.value}>
                    {o.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>

            {(data?.sources.length ?? 0) > 0 && (
              <Tabs value={source} onValueChange={setSource}>
                <TabsList>
                  <TabsTrigger value="">Any agent</TabsTrigger>
                  {data?.sources.map((s) => (
                    <TabsTrigger key={s} value={s}>
                      {s.replace(/_/g, " ")}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            )}
          </div>

          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : writes.length === 0 ? (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                Nothing was changed in this window.
              </CardContent>
            </Card>
          ) : (
            <div className="flex flex-col gap-2">
              {writes.map((w) => (
                <Card key={w.id}>
                  <CardContent className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-3">
                    <Badge variant={outcomeVariant(w.outcome)}>
                      {outcomeLabel(w.outcome)}
                    </Badge>
                    <span className="font-mono text-sm">{w.tool}</span>
                    {w.target && (
                      <span className="text-sm text-muted-foreground">{w.target}</span>
                    )}
                    <span className="ml-auto text-xs text-muted-foreground">
                      {w.source.replace(/_/g, " ")}
                      {w.created_at ? ` · ${formatDate(w.created_at)}` : ""}
                    </span>
                    {w.detail && (
                      <span className="w-full text-xs text-muted-foreground">{w.detail}</span>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
