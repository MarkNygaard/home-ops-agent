"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
} from "@/components/ui/select"
import { SiteHeader } from "@/components/site-header"
import { useSettings } from "@/hooks/use-settings"
import { updateSetting } from "@/lib/api"
import { NOTIFY_LEVELS } from "@/lib/constants"

export default function NotificationSettingsPage() {
  const { data: settings, mutate } = useSettings()

  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState("")
  const [error, setError] = useState("")

  const notifyLevel = overrides.notify_level ?? settings?.notify_level ?? "outcomes"
  const ntfyUrl = overrides.ntfy_url ?? settings?.ntfy_url ?? ""
  const ntfyTopic = overrides.ntfy_agent_topic ?? settings?.ntfy_agent_topic ?? ""
  const ntfyToken = overrides.ntfy_token ?? ""
  const subscriptionTopics =
    overrides.ntfy_topics ?? settings?.ntfy_topics ?? "alertmanager,gatus"

  function setField(key: string, value: string) {
    setOverrides((prev) => ({ ...prev, [key]: value }))
    setDirty(true)
  }

  async function handleSave() {
    setError("")
    const writes: Promise<{ error?: string }>[] = [
      updateSetting("notify_level", notifyLevel),
      updateSetting("ntfy_url", ntfyUrl),
      updateSetting("ntfy_agent_topic", ntfyTopic),
      updateSetting("ntfy_topics", subscriptionTopics),
    ]
    // Only write the token when something was typed, so saving the page does
    // not overwrite a stored secret with an empty field.
    if (ntfyToken.trim()) {
      writes.push(updateSetting("ntfy_token", ntfyToken.trim()))
    }

    const results = await Promise.all(writes)
    const failed = results.find((r) => r?.error)
    if (failed?.error) {
      setError(failed.error)
      return
    }

    setDirty(false)
    setOverrides({})
    setStatus("Saved")
    setTimeout(() => setStatus(""), 3000)
    mutate()
  }

  const currentLevel = NOTIFY_LEVELS.find((l) => l.value === notifyLevel)

  return (
    <>
      <SiteHeader title="Notifications" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-medium">How much to send</h3>
            <p className="text-xs text-muted-foreground">
              Notifications are classified by how much they need you. Anything
              that failed, or needs your review, is always sent regardless of
              this setting.
            </p>
            <div className="max-w-md">
              <Select
                value={notifyLevel}
                onValueChange={(v) => setField("notify_level", v as string)}
              >
                <SelectTrigger>{currentLevel?.label ?? notifyLevel}</SelectTrigger>
                <SelectContent>
                  {NOTIFY_LEVELS.map((l) => (
                    <SelectItem key={l.value} value={l.value}>
                      {l.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {currentLevel && (
              <p className="text-xs text-muted-foreground">
                {currentLevel.description}
              </p>
            )}
          </div>

          <Separator />

          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-medium">Where the agent sends</h3>
            <p className="text-xs text-muted-foreground">
              Outgoing notifications: PR reviews, merges, alert diagnostics.
            </p>
            <div className="flex flex-col gap-2">
              <Label htmlFor="ntfy-url">ntfy server URL</Label>
              <Input
                id="ntfy-url"
                value={ntfyUrl}
                onChange={(e) => setField("ntfy_url", e.target.value)}
                placeholder="http://ntfy.monitoring.svc.cluster.local"
                className="max-w-lg"
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="ntfy-topic">Publish topic</Label>
              <Input
                id="ntfy-topic"
                value={ntfyTopic}
                onChange={(e) => setField("ntfy_agent_topic", e.target.value)}
                placeholder="home-ops-agent"
                className="max-w-sm"
              />
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <Label htmlFor="ntfy-token">Access token</Label>
                <span className="text-xs text-muted-foreground">optional</span>
                {/* "No auth" rather than "Not set": an unauthenticated ntfy
                    server is a normal setup, not an incomplete one. */}
                <Badge
                  variant={settings?.ntfy_token_configured ? "default" : "secondary"}
                >
                  {settings?.ntfy_token_configured ? "Configured" : "No auth"}
                </Badge>
                {settings?.ntfy_token_hint && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {settings.ntfy_token_hint}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Only needed if your ntfy server requires authentication. Leave it
                empty for an open server — notifications still work, but anyone
                who knows the topic name can read them.
              </p>
              <Input
                id="ntfy-token"
                type="password"
                value={ntfyToken}
                onChange={(e) => setField("ntfy_token", e.target.value)}
                placeholder={
                  settings?.ntfy_token_configured
                    ? "Leave blank to keep the stored token"
                    : "tk_… — leave empty for no authentication"
                }
                className="max-w-lg"
              />
            </div>
          </div>

          <Separator />

          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-medium">What the agent listens to</h3>
            <p className="text-xs text-muted-foreground">
              Incoming alerts. The agent subscribes to these topics and triages
              what arrives — this is not where it publishes.
            </p>
            <div className="flex flex-col gap-2">
              <Label htmlFor="ntfy-topics">
                Subscription topics (comma-separated)
              </Label>
              <Input
                id="ntfy-topics"
                value={subscriptionTopics}
                onChange={(e) => setField("ntfy_topics", e.target.value)}
                placeholder="alertmanager,gatus"
                className="max-w-lg"
              />
            </div>
          </div>

          <Separator />

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={!dirty}>
              Save
            </Button>
            {status && <span className="text-sm text-green-500">{status}</span>}
            {error && <span className="text-sm text-destructive">{error}</span>}
          </div>
        </div>
      </div>
    </>
  )
}
