"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SiteHeader } from "@/components/site-header"
import { useSettings } from "@/hooks/use-settings"
import { updateSetting } from "@/lib/api"
import { KillSwitch } from "@/components/settings/kill-switch"
import { IntervalsSection } from "@/components/settings/intervals-section"

export default function SettingsGeneralPage() {
  const { data: settings, mutate: mutateSettings } = useSettings()

  const [dirty, setDirty] = useState(false)
  const [statusMsg, setStatusMsg] = useState("")
  const [overrides, setOverrides] = useState<Record<string, string>>({})

  // Derive values from settings with local overrides
  const prMode = overrides.pr_mode ?? settings?.pr_mode ?? "comment_only"
  const alertCooldown = overrides.alert_cooldown ?? String(settings?.alert_cooldown_seconds ?? "900")
  const ntfyTopics = overrides.ntfy_topics ?? settings?.ntfy_topics ?? "alertmanager,gatus"
  const prInterval = overrides.pr_interval ?? String(settings?.pr_check_interval_seconds ?? "1800")

  function setField(key: string, value: string) {
    setOverrides((prev) => ({ ...prev, [key]: value }))
    setDirty(true)
  }

  function showStatus(msg: string) {
    setStatusMsg(msg)
    setTimeout(() => setStatusMsg(""), 3000)
  }

  async function handleSave() {
    await Promise.all([
      updateSetting("pr_mode", prMode),
      updateSetting("alert_cooldown_seconds", alertCooldown),
      updateSetting("ntfy_topics", ntfyTopics),
      updateSetting("pr_check_interval_seconds", prInterval),
    ])
    setDirty(false)
    setOverrides({})
    showStatus("Settings saved")
    mutateSettings()
  }

  return (
    <>
      <SiteHeader title="General Settings" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <KillSwitch
            settings={settings ?? null}
            onToggle={() => mutateSettings()}
          />

          <Separator />

          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-medium">PR Review Mode</h3>
            <div className="flex flex-col gap-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="pr_mode"
                  value="comment_only"
                  checked={prMode === "comment_only"}
                  onChange={(e) => setField("pr_mode", e.target.value)}
                />
                Comment Only
                <span className="text-muted-foreground">
                  — Agent reviews PRs and posts comments. You merge manually.
                </span>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="pr_mode"
                  value="auto_merge"
                  checked={prMode === "auto_merge"}
                  onChange={(e) => setField("pr_mode", e.target.value)}
                />
                Auto-Merge
                <span className="text-muted-foreground">
                  — Agent merges safe Renovate patch/digest PRs with passing CI.
                </span>
              </label>
            </div>
          </div>

          <Separator />

          <IntervalsSection
            alertCooldown={alertCooldown}
            ntfyTopics={ntfyTopics}
            prInterval={prInterval}
            onAlertCooldownChange={(v) => setField("alert_cooldown", v)}
            onNtfyTopicsChange={(v) => setField("ntfy_topics", v)}
            onPrIntervalChange={(v) => setField("pr_interval", v)}
          />

          <Separator />

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={!dirty}>
              Save Settings
            </Button>
            {statusMsg && (
              <span className="text-sm text-green-500">{statusMsg}</span>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
