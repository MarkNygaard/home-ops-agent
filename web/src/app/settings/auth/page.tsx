"use client"

import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { SiteHeader } from "@/components/site-header"
import { useSettings } from "@/hooks/use-settings"
import { updateSetting } from "@/lib/api"
import { AuthSection } from "@/components/settings/auth-section"

export default function SettingsAuthPage() {
  const { data: settings, mutate: mutateSettings } = useSettings()

  const [dirty, setDirty] = useState(false)
  const [statusMsg, setStatusMsg] = useState("")
  const [authMethod, setAuthMethod] = useState("api_key")

  useEffect(() => {
    if (!settings) return
    setAuthMethod(settings.auth_method)
  }, [settings])

  const markDirty = useCallback(() => setDirty(true), [])

  function showStatus(msg: string) {
    setStatusMsg(msg)
    setTimeout(() => setStatusMsg(""), 3000)
  }

  async function handleSave() {
    await updateSetting("auth_method", authMethod)
    setDirty(false)
    showStatus("Auth settings saved")
    mutateSettings()
  }

  return (
    <>
      <SiteHeader title="Authentication" />
      <div className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
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

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={!dirty}>
              Save Auth Settings
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
