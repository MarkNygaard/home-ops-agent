"use client"

import {
  IconGitPullRequest,
  IconAlertTriangle,
} from "@tabler/icons-react"
import { Badge } from "@/components/ui/badge"
import { useSettings } from "@/hooks/use-settings"
import { MODEL_MIGRATION } from "@/lib/constants"
import { cn } from "@/lib/utils"

const FLOW_DEFS = [
  {
    key: "pr_review",
    name: "PR Review",
    icon: IconGitPullRequest,
    description: "Reviews PRs, auto-merges safe patches, fixes breaking changes",
    models: ["pr_review", "code_fix"],
  },
  {
    key: "alert",
    name: "Alert",
    icon: IconAlertTriangle,
    description: "Triages alerts, fixes issues, notifies when needed",
    models: ["alert_triage", "alert_fix"],
  },
] as const

interface AgentCardsProps {
  activeAgent: string
  onSelect: (key: string) => void
}

export function AgentCards({ activeAgent, onSelect }: AgentCardsProps) {
  const { data: settings } = useSettings()

  function getModelLabel(key: string): string {
    const raw = settings?.models?.[key]
    if (!raw) return "Sonnet 4.6"
    const migrated = MODEL_MIGRATION[raw] || raw
    if (migrated.includes("haiku")) return "Haiku 4.5"
    if (migrated.includes("opus")) return "Opus 4.6"
    return "Sonnet 4.6"
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {FLOW_DEFS.map((flow) => {
        const isActive = activeAgent === flow.key
        return (
          <div
            key={flow.key}
            className="cursor-pointer"
            onClick={() => onSelect(flow.key)}
          >
            <div
              className={cn(
                "rounded-xl p-0.5 transition-all",
                isActive
                  ? "bg-linear-to-br from-accent-orange-light to-accent-orange"
                  : "bg-border hover:bg-muted-foreground/20"
              )}
            >
              <div className="rounded-[10px] bg-card p-5">
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <flow.icon
                      className={cn(
                        "size-6",
                        isActive
                          ? "text-accent-orange"
                          : "text-muted-foreground"
                      )}
                    />
                    <span className="text-sm font-semibold">{flow.name}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {flow.description}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {flow.models.map((m) => (
                      <Badge
                        key={m}
                        variant="accent"
                        className="text-[0.6rem]"
                      >
                        {m.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}:{" "}
                        {getModelLabel(m)}
                      </Badge>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
