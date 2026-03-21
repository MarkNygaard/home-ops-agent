"use client"

import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { SiteHeader } from "@/components/site-header"
import { useMemories } from "@/hooks/use-memories"
import { deleteMemory } from "@/lib/api"
import { formatDate } from "@/lib/utils"
import { CATEGORY_COLORS } from "@/lib/constants"

export default function MemoriesPage() {
  const { data: memories, mutate } = useMemories()

  async function handleDelete(id: number) {
    await deleteMemory(id)
    mutate()
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
      <div className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
        <p className="mb-4 text-sm text-muted-foreground">
          Facts the agent remembers from previous conversations. These are
          included in the system prompt for all future interactions.
        </p>
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
    </>
  )
}
