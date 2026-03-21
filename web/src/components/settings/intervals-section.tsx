"use client"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface IntervalsSectionProps {
  alertCooldown: string
  ntfyTopics: string
  prInterval: string
  onAlertCooldownChange: (v: string) => void
  onNtfyTopicsChange: (v: string) => void
  onPrIntervalChange: (v: string) => void
}

export function IntervalsSection({
  alertCooldown,
  ntfyTopics,
  prInterval,
  onAlertCooldownChange,
  onNtfyTopicsChange,
  onPrIntervalChange,
}: IntervalsSectionProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        <h3 className="text-sm font-medium">Alert Settings</h3>
        <div className="flex flex-col gap-2">
          <Label htmlFor="alert-cooldown">Cooldown (seconds)</Label>
          <Input
            id="alert-cooldown"
            type="number"
            value={alertCooldown}
            onChange={(e) => onAlertCooldownChange(e.target.value)}
            min={60}
            max={3600}
            className="max-w-48"
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="ntfy-topics">ntfy Topics (comma-separated)</Label>
          <Input
            id="ntfy-topics"
            type="text"
            value={ntfyTopics}
            onChange={(e) => onNtfyTopicsChange(e.target.value)}
            className="max-w-sm"
          />
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <h3 className="text-sm font-medium">PR Monitor</h3>
        <div className="flex flex-col gap-2">
          <Label htmlFor="pr-interval">Check interval (seconds)</Label>
          <Input
            id="pr-interval"
            type="number"
            value={prInterval}
            onChange={(e) => onPrIntervalChange(e.target.value)}
            min={300}
            max={7200}
            className="max-w-48"
          />
        </div>
      </div>
    </div>
  )
}
