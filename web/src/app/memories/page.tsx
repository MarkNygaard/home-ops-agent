"use client"

import { useState } from "react"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { SiteHeader } from "@/components/site-header"
import { useMemories } from "@/hooks/use-memories"
import { createMemory, deleteMemory } from "@/lib/api"
import { formatDate } from "@/lib/utils"
import { CATEGORY_COLORS, MEMORY_CATEGORIES } from "@/lib/constants"

export default function MemoriesPage() {
  const { data: memories, mutate } = useMemories()
  const [adding, setAdding] = useState(false)
  const [content, setContent] = useState("")
  const [category, setCategory] = useState("knowledge")
  const [error, setError] = useState("")
  const [saving, setSaving] = useState(false)

  async function handleDelete(id: number) {
    await deleteMemory(id)
    mutate()
  }

  async function handleAdd() {
    if (!content.trim()) return
    setSaving(true)
    const res = await createMemory(content.trim(), category)
    setSaving(false)
    if (res?.error) {
      setError(res.error)
      return
    }
    setContent("")
    setCategory("knowledge")
    setError("")
    setAdding(false)
    mutate()
  }

  function cancelAdd() {
    setAdding(false)
    setContent("")
    setError("")
  }

  function categoryVariant(category: string) {
    const mapping = CATEGORY_COLORS[category] || CATEGORY_COLORS.general
    if (mapping === "destructive") return "destructive" as const
    if (mapping === "outline") return "outline" as const
    if (mapping === "default") return "default" as const
    return "secondary" as const
  }

  return (
    <>
      <SiteHeader title="Memories" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto max-w-6xl">
          <div className="mb-4 flex items-start justify-between gap-4">
            <p className="text-sm text-muted-foreground">
              Facts the agent remembers from previous conversations. These are
              included in the system prompt for all future interactions. The
              agent only extracts memories from chats, so add anything it
              learned elsewhere yourself.
            </p>
            {!adding && (
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={() => setAdding(true)}
              >
                <Plus className="size-3.5" />
                Add memory
              </Button>
            )}
          </div>

          {adding && (
            <Card className="mb-4">
              <CardContent className="flex flex-col gap-3">
                <Textarea
                  autoFocus
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="A durable fact about how the cluster is built — not what is broken right now."
                  rows={3}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <div className="min-w-64">
                    <Select
                      value={category}
                      onValueChange={(val) => setCategory(val as string)}
                    >
                      <SelectTrigger>
                        {MEMORY_CATEGORIES.find((c) => c.value === category)
                          ?.label ?? "Select category"}
                      </SelectTrigger>
                      <SelectContent>
                        {MEMORY_CATEGORIES.map((c) => (
                          <SelectItem key={c.value} value={c.value}>
                            {c.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button onClick={handleAdd} disabled={saving || !content.trim()}>
                    {saving ? "Saving..." : "Save"}
                  </Button>
                  <Button variant="ghost" onClick={cancelAdd}>
                    Cancel
                  </Button>
                </div>
                {error && (
                  <span className="text-xs text-destructive">{error}</span>
                )}
              </CardContent>
            </Card>
          )}

          {!memories || memories.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No memories yet. The agent will extract key facts from conversations
              automatically.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {memories.map((mem) => (
                <Card key={mem.id}>
                  <CardContent>
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-2">
                          <Badge variant={categoryVariant(mem.category)}>
                            {mem.category}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {formatDate(mem.created_at)}
                          </span>
                        </div>
                        <p className="text-sm">{mem.content}</p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => handleDelete(mem.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
