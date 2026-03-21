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

interface PromptModalProps {
  promptKey: string
  prompt: PromptInfo | undefined
  label: string
  description: string
  onClose: () => void
  onSaved: () => void
}

export function PromptModal({
  promptKey,
  prompt,
  label,
  description,
  onClose,
  onSaved,
}: PromptModalProps) {
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
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {label}
            {prompt?.is_customized && (
              <Badge variant="outline">Customized</Badge>
            )}
          </DialogTitle>
          {description && (
            <DialogDescription>{description}</DialogDescription>
          )}
        </DialogHeader>

        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="min-h-64"
          placeholder="Enter prompt..."
        />

        <DialogFooter>
          {prompt?.is_customized && (
            <Button variant="outline" onClick={handleReset}>
              Reset to Default
            </Button>
          )}
          <Button onClick={handleSave}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
