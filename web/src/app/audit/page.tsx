"use client"

import { useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SiteHeader } from "@/components/site-header"
import { useAudit } from "@/hooks/use-audit"
import { cn } from "@/lib/utils"
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

// What each tool did, in a word, so the row reads as a sentence instead of an
// API call. The tool name is still shown — it is what you search the code for.
const VERBS: Record<string, string> = {
  github_merge_pr: "merged",
  github_create_pr_comment: "commented on",
  github_create_pr: "opened",
  github_create_branch: "branched",
  github_create_commit: "committed",
  workspace_commit: "committed",
  code_fix: "fixed",
  k8s_restart_workload: "restarted",
  k8s_delete_pod: "deleted pod",
  flux_reconcile: "reconciled",
  flux_suspend: "suspended",
  flux_resume: "resumed",
  ntfy_publish: "notified",
}

// The PR these tools name, so a comment, a merge and its notification group
// under one heading instead of reading as three unrelated events.
function subjectOf(w: WriteRecord): string | null {
  const fromTarget = w.target.match(/^#?(\d{2,6})$/)
  if (fromTarget) return `PR #${fromTarget[1]}`
  const fromDetail = `${w.target} ${w.detail}`.match(/PR #(\d{2,6})/)
  if (fromDetail) return `PR #${fromDetail[1]}`
  return null
}

function dayLabel(iso: string | null): string {
  if (!iso) return "Unknown"
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, today)) return "Today"
  if (same(d, yesterday)) return "Yesterday"
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
}

function timeLabel(iso: string | null): string {
  if (!iso) return "--:--"
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
}

const DOT: Record<string, string> = {
  ok: "bg-green-500",
  blocked: "bg-amber-500",
  error: "bg-red-500",
}

/** Consecutive rows about the same PR, kept in order. */
type Group = { key: string; subject: string | null; writes: WriteRecord[] }

function group(writes: WriteRecord[]): { day: string; groups: Group[] }[] {
  const days: { day: string; groups: Group[] }[] = []

  for (const w of writes) {
    const day = dayLabel(w.created_at)
    let bucket = days.at(-1)
    if (!bucket || bucket.day !== day) {
      bucket = { day, groups: [] }
      days.push(bucket)
    }

    const subject = subjectOf(w)
    const last = bucket.groups.at(-1)
    // Only consecutive rows group: two visits to the same PR an hour apart are
    // two events, and merging them would hide the gap.
    if (last && subject !== null && last.subject === subject) {
      last.writes.push(w)
    } else {
      bucket.groups.push({ key: `${w.id}`, subject, writes: [w] })
    }
  }

  return days
}

function Row({ write }: { write: WriteRecord }) {
  const verb = VERBS[write.tool]
  // "ok" as a detail line is noise on every single row; anything else is the
  // reason something did not happen, which is the point of the page.
  const detail = write.detail && write.detail !== "ok" ? write.detail : null

  return (
    <div className="flex items-baseline gap-3 py-2 text-sm">
      <span className="w-12 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
        {timeLabel(write.created_at)}
      </span>
      <span
        className={cn("size-1.5 shrink-0 translate-y-[-1px] rounded-full", DOT[write.outcome])}
        title={write.outcome}
      />
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        {verb && <span className="shrink-0">{verb}</span>}
        <span className="truncate text-muted-foreground" title={write.target}>
          {write.target}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground/60">{write.tool}</span>
        {write.outcome !== "ok" && (
          <span
            className={cn(
              "shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium",
              write.outcome === "blocked"
                ? "bg-amber-500/15 text-amber-500"
                : "bg-red-500/15 text-red-500"
            )}
          >
            {write.outcome === "blocked" ? "blocked by a guardrail" : "failed"}
          </span>
        )}
        {detail && <span className="w-full text-xs text-muted-foreground/80">{detail}</span>}
      </div>
      <span className="shrink-0 text-xs text-muted-foreground/70">
        {write.source.replace(/_/g, " ")}
      </span>
    </div>
  )
}

function Stat({ value, label, dot }: { value: number; label: string; dot?: string }) {
  return (
    <div className="flex items-baseline gap-2">
      {dot && <span className={cn("size-2 rounded-full", dot)} />}
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      <span className="text-sm text-muted-foreground">{label}</span>
    </div>
  )
}

export default function AuditPage() {
  const [days, setDays] = useState(7)
  const [outcome, setOutcome] = useState("")
  const [source, setSource] = useState("")
  const { data, isLoading } = useAudit(days, outcome, source)

  const writes: WriteRecord[] = data?.writes ?? []
  const byDay = group(writes)

  return (
    <>
      <SiteHeader title="Write Audit" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-5xl flex-col gap-6">
          <p className="max-w-3xl text-sm text-muted-foreground">
            Every change the agent made — restarts, reconciles, commits, pull
            requests, merges — across all runs, newest first. Reads are not
            listed: they are the overwhelming majority of what the agent does,
            and including them would bury the lines that matter.
          </p>

          <div className="flex flex-wrap items-center gap-8">
            <Stat value={data?.counts.total ?? 0} label="writes" />
            <Stat
              value={data?.counts.blocked ?? 0}
              label="blocked by a guardrail"
              dot="bg-amber-500"
            />
            <Stat value={data?.counts.error ?? 0} label="failed" dot="bg-red-500" />
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

            {(data?.sources.length ?? 0) > 1 && (
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
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                Nothing was changed in this window.
              </CardContent>
            </Card>
          ) : (
            <div className="flex flex-col gap-6">
              {byDay.map((bucket) => (
                <div key={bucket.day} className="flex flex-col gap-2">
                  <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    {bucket.day}
                  </h2>
                  <Card className="py-0">
                    <CardContent className="divide-y divide-border/60 px-4 py-0">
                      {bucket.groups.map((g) => (
                        <div key={g.key} className="py-1.5">
                          {g.subject && g.writes.length > 1 && (
                            <div className="pt-1.5 text-xs font-medium text-accent-orange">
                              {g.subject}
                            </div>
                          )}
                          {g.writes.map((w) => (
                            <Row key={w.id} write={w} />
                          ))}
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
