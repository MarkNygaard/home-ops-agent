"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { SlidePanel } from "@/components/slide-panel"
import { updateSetting, resetPrompt } from "@/lib/api"
import type { PromptInfo } from "@/lib/types"

interface PromptPanelProps {
  promptKey: string
  prompt: PromptInfo | undefined
  label: string
  description: string
  onClose: () => void
  onSaved: () => void
}

export function PromptPanel({
  promptKey,
  prompt,
  label,
  description,
  onClose,
  onSaved,
}: PromptPanelProps) {
  const [value, setValue] = useState(
    prompt?.custom || prompt?.default || ""
  )

  async function handleSave() {
    await updateSetting(`prompt_${promptKey}`, value)
    onSaved()
    onClose()
  }

  async function handleReset() {
    await resetPrompt(promptKey)
    onSaved()
    onClose()
  }

  return (
    <SlidePanel
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={label}
      description={description}
      footer={
        <div className="flex w-full items-center gap-2">
          {prompt?.is_customized && (
            <Button variant="outline" onClick={handleReset}>
              Reset to Default
            </Button>
          )}
          <Button onClick={handleSave} className="ml-auto">
            Save
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        {prompt?.is_customized && (
          <Badge variant="outline" className="w-fit">
            Customized
          </Badge>
        )}
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="min-h-80"
          placeholder="Enter prompt..."
        />
      </div>
    </SlidePanel>
  )
}
