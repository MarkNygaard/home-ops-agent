"use client"

import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { SiteHeader } from "@/components/site-header"
import { useSettings } from "@/hooks/use-settings"
import { useSkills } from "@/hooks/use-skills"
import { usePrompts } from "@/hooks/use-prompts"
import { updateSetting } from "@/lib/api"
import { MODEL_MIGRATION } from "@/lib/constants"
import { KillSwitch } from "@/components/settings/kill-switch"
import { SkillsSection } from "@/components/settings/skills-section"
import { AuthSection } from "@/components/settings/auth-section"
import { AgentsSection } from "@/components/settings/agents-section"
import { IntervalsSection } from "@/components/settings/intervals-section"
import { Separator } from "@/components/ui/separator"

export default function SettingsPage() {
  const { data: settings, mutate: mutateSettings } = useSettings()
  const { data: skills, mutate: mutateSkills } = useSkills()
  const { data: prompts, mutate: mutatePrompts } = usePrompts()

  const [dirty, setDirty] = useState(false)
  const [statusMsg, setStatusMsg] = useState("")

  // Local form state
  const [prMode, setPrMode] = useState("comment_only")
  const [authMethod, setAuthMethod] = useState("api_key")
  const [alertCooldown, setAlertCooldown] = useState("900")
  const [ntfyTopics, setNtfyTopics] = useState("alertmanager,gatus")
  const [prInterval, setPrInterval] = useState("1800")
  const [models, setModels] = useState<Record<string, string>>({})

  // Populate from loaded settings
  useEffect(() => {
    if (!settings) return
    setPrMode(settings.pr_mode)
    setAuthMethod(settings.auth_method)
    setAlertCooldown(String(settings.alert_cooldown_seconds))
    setNtfyTopics(settings.ntfy_topics)
    setPrInterval(String(settings.pr_check_interval_seconds))
    if (settings.models) {
      const mapped: Record<string, string> = {}
      for (const [task, model] of Object.entries(settings.models)) {
        mapped[task] = MODEL_MIGRATION[model] || model
      }
      setModels(mapped)
    }
  }, [settings])

  const markDirty = useCallback(() => setDirty(true), [])

  function showStatus(msg: string) {
    setStatusMsg(msg)
    setTimeout(() => setStatusMsg(""), 3000)
  }

  async function handleSaveAll() {
    const modelTasks = [
      "pr_review",
      "alert_triage",
      "alert_fix",
      "code_fix",
      "chat",
    ]
    const modelSaves = modelTasks
      .filter((task) => models[task])
      .map((task) => updateSetting(`model_${task}`, models[task]))

    await Promise.all([
      updateSetting("pr_mode", prMode),
      updateSetting("auth_method", authMethod),
      updateSetting("alert_cooldown_seconds", alertCooldown),
      updateSetting("ntfy_topics", ntfyTopics),
      updateSetting("pr_check_interval_seconds", prInterval),
      ...modelSaves,
    ])
    setDirty(false)
    showStatus("Settings saved")
    mutateSettings()
  }

  return (
    <>
      <SiteHeader title="Settings" />
      <div className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          <KillSwitch
            settings={settings ?? null}
            onToggle={() => {
              mutateSettings()
            }}
          />

          <Separator />

          <SkillsSection
            skills={skills ?? []}
            onUpdate={() => {
              mutateSkills()
              showStatus("Skill updated")
            }}
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
                  onChange={(e) => {
                    setPrMode(e.target.value)
                    markDirty()
                  }}
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
                  onChange={(e) => {
                    setPrMode(e.target.value)
                    markDirty()
                  }}
                />
                Auto-Merge
                <span className="text-muted-foreground">
                  — Agent merges safe Renovate patch/digest PRs with passing CI.
                </span>
              </label>
            </div>
          </div>

          <Separator />

          <AuthSection
            settings={settings ?? null}
            authMethod={authMethod}
            onAuthMethodChange={(v) => {
              setAuthMethod(v)
              markDirty()
            }}
            onSaved={() => {
              mutateSettings()
              showStatus("API key saved")
            }}
          />

          <Separator />

          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-medium">Cluster Context</h3>
            <p className="text-sm text-muted-foreground">
              Shared system prompt prepended to all agents. Describe your cluster
              setup, IPs, domain, and infrastructure.
            </p>
            <AgentsSection
              prompts={prompts ?? null}
              models={models}
              onModelChange={(task, model) => {
                setModels((prev) => ({ ...prev, [task]: model }))
                markDirty()
              }}
              onPromptSaved={() => {
                mutatePrompts()
                showStatus("Prompt saved")
              }}
            />
          </div>

          <Separator />

          <IntervalsSection
            alertCooldown={alertCooldown}
            ntfyTopics={ntfyTopics}
            prInterval={prInterval}
            onAlertCooldownChange={(v) => {
              setAlertCooldown(v)
              markDirty()
            }}
            onNtfyTopicsChange={(v) => {
              setNtfyTopics(v)
              markDirty()
            }}
            onPrIntervalChange={(v) => {
              setPrInterval(v)
              markDirty()
            }}
          />

          <Separator />

          <div className="flex items-center gap-3">
            <Button onClick={handleSaveAll} disabled={!dirty}>
              Save All Settings
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
