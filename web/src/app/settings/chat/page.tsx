"use client"

import { useState } from "react"
import { Plus, X, RotateCcw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SiteHeader } from "@/components/site-header"
import { useSettings } from "@/hooks/use-settings"
import { updateSetting } from "@/lib/api"

const DEFAULT_SUGGESTIONS = [
  "What pods are failing?",
  "Show me recent alerts",
  "List pending PRs",
  "Check cluster health",
]

export default function SettingsChatPage() {
  const { data: settings, mutate: mutateSettings } = useSettings()
  const [dirty, setDirty] = useState(false)
  const [statusMsg, setStatusMsg] = useState("")
  const [newSuggestion, setNewSuggestion] = useState("")

  // Parse suggestions from settings (pipe-separated to allow commas in text)
  const savedSuggestions = settings?.chat_suggestions
    ? settings.chat_suggestions.split("|").map((s) => s.trim()).filter(Boolean)
    : []

  const [suggestions, setSuggestions] = useState<string[] | null>(null)
  const current = suggestions ?? savedSuggestions

  function updateSuggestions(next: string[]) {
    setSuggestions(next)
    setDirty(true)
  }

  function handleRemove(index: number) {
    updateSuggestions(current.filter((_, i) => i !== index))
  }

  function handleAdd() {
    const text = newSuggestion.trim()
    if (!text || current.includes(text)) return
    updateSuggestions([...current, text])
    setNewSuggestion("")
  }

  function handleRestoreDefaults() {
    updateSuggestions([...DEFAULT_SUGGESTIONS])
  }

  async function handleSave() {
    await updateSetting("chat_suggestions", current.join("|"))
    setDirty(false)
    setSuggestions(null)
    setStatusMsg("Saved")
    setTimeout(() => setStatusMsg(""), 3000)
    mutateSettings()
  }

  return (
    <>
      <SiteHeader title="Chat Settings" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium">Chat Suggestions</h3>
                <p className="text-xs text-muted-foreground">
                  Quick prompts shown on the empty chat screen. Leave empty to hide suggestions.
                </p>
              </div>
              <Button variant="ghost" size="sm" onClick={handleRestoreDefaults}>
                <RotateCcw className="mr-1.5 size-3.5" />
                Restore defaults
              </Button>
            </div>

            <div className="flex flex-col gap-2">
              {current.map((suggestion, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2"
                >
                  <span className="flex-1 text-sm">{suggestion}</span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0"
                    onClick={() => handleRemove(i)}
                  >
                    <X className="size-3.5" />
                  </Button>
                </div>
              ))}
              {current.length === 0 && (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  No suggestions configured. Add one below.
                </p>
              )}
            </div>

            <div className="flex gap-2">
              <Input
                value={newSuggestion}
                onChange={(e) => setNewSuggestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault()
                    handleAdd()
                  }
                }}
                placeholder="Add a new suggestion..."
                className="flex-1"
              />
              <Button
                variant="outline"
                size="icon"
                onClick={handleAdd}
                disabled={!newSuggestion.trim()}
              >
                <Plus className="size-4" />
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={!dirty}>
              Save
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
