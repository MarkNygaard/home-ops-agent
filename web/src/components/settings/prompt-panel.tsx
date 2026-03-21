"use client"

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
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
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="fixed top-6 right-6 left-auto bottom-auto translate-x-0 translate-y-0 sm:max-w-lg md:max-w-xl lg:max-w-2xl max-h-[calc(100vh-3rem)] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <DialogTitle>{label}</DialogTitle>
            {prompt?.is_customized && (
              <Badge variant="accent">Customized</Badge>
            )}
          </div>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="min-h-80 font-mono text-xs"
          placeholder="Enter prompt..."
        />

        <DialogFooter>
          {prompt?.is_customized && (
            <Button variant="outline" onClick={handleReset}>
              Reset to Default
            </Button>
          )}
          <Button onClick={handleSave}>Save Prompt</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
