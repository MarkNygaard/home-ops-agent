/** Reading an agent run's record: shared by History, the dashboard and anything
 * else that shows what the agent did.
 *
 * A task summary is 500 characters of whatever the model wrote — headings, bold
 * markers, table pipes — with the worker's verdict tag on the front. Rendered
 * raw it produced rows like
 * "[SAFE_TO_MERGE] ## Review Complete ✅ | Aspect | Result | |----|----|".
 * These turn that into something a person can scan.
 */

import type { HistoryItem } from "./types"

export const TYPE_LABEL: Record<string, string> = {
  pr_review: "review",
  pr_merge: "merge",
  alert_response: "alert",
  alert_triage: "triage",
  alert_fix: "alert fix",
  code_fix: "code fix",
  cluster_fix: "cluster fix",
  chat: "chat",
  user_chat: "chat",
}

/** The verdict a run reached, as the worker writes it into the summary prefix. */
export const VERDICT_STYLE: Record<string, string> = {
  SAFE_TO_MERGE: "bg-green-500/15 text-green-500",
  NEEDS_REVIEW: "bg-amber-500/15 text-amber-500",
  NEEDS_FIX: "bg-orange-500/15 text-orange-500",
  "RAN OUT OF TURNS": "bg-red-500/15 text-red-500",
  "Deep Review": "bg-accent-orange/15 text-accent-orange",
}

/** Leading `[…]` tags, and the text with them removed. */
export function splitTags(summary: string): { tags: string[]; rest: string } {
  const tags: string[] = []
  let rest = (summary ?? "").trimStart()
  for (;;) {
    const match = rest.match(/^\[([^\]]{1,24})\]\s*/)
    if (!match) break
    tags.push(match[1])
    rest = rest.slice(match[0].length)
  }
  return { tags, rest }
}

/** Model markdown as one readable line. */
export function plain(text: string): string {
  return (text ?? "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/^\s*\|.*$/gm, " ")
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/^\s*[-*]\s+/gm, "• ")
    .replace(/[*_`>]/g, "")
    .replace(/\s+/g, " ")
    .trim()
}

/** The PR title where the summary carries it, else the trigger.
 *
 * A merge records "Auto-merged: fix(container): update image ghcr.io/x ( a → b )",
 * so the row can say what was updated instead of only its number.
 */
export function titleOf(item: HistoryItem): string {
  const { rest } = splitTags(item.summary ?? "")
  const merged = rest.match(/^Auto-merged(?: \([^)]*\))?:\s*(.+)$/)
  if (merged) return plain(merged[1])
  return item.trigger
}

export function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, today)) return "Today"
  if (same(d, yesterday)) return "Yesterday"
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
}

/** "just now", "12m", "3h", "2d".
 *
 * For the dashboard, where the question is "what has happened lately" rather
 * than "what happened at 00:34".
 */
export function relativeTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 90) return "just now"
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}
