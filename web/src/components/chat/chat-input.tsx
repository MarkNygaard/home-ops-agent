"use client"

import { useState, type KeyboardEvent } from "react"
import { Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

interface ChatInputProps {
  onSend: (text: string) => void
  disabled?: boolean
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState("")

  function handleSend() {
    const text = value.trim()
    if (!text) return
    onSend(text)
    setValue("")
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t px-6 py-3 lg:px-8">
      <div className="mx-auto flex max-w-5xl gap-2">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your cluster..."
          rows={2}
          className="min-h-10 flex-1 resize-none"
        />
        <Button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          size="icon"
        >
          <Send className="size-4" />
        </Button>
      </div>
    </div>
  )
}
