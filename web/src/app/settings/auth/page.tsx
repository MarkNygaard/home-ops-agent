"use client"

import { useState } from "react"
import { SiteHeader } from "@/components/site-header"
import { useSettings } from "@/hooks/use-settings"
import { AuthSection } from "@/components/settings/auth-section"

export default function SettingsAuthPage() {
  const { data: settings, mutate: mutateSettings } = useSettings()
  const [statusMsg, setStatusMsg] = useState("")

  function showStatus(msg: string) {
    setStatusMsg(msg)
    setTimeout(() => setStatusMsg(""), 3000)
  }

  return (
    <>
      <SiteHeader title="Authentication" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <AuthSection
            settings={settings ?? null}
            onSaved={() => {
              mutateSettings()
              showStatus("Saved")
            }}
          />
          {statusMsg && (
            <span className="text-sm text-green-500">{statusMsg}</span>
          )}
        </div>
      </div>
    </>
  )
}
