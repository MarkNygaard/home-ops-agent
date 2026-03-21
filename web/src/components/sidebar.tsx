"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { MessageSquare, History, Brain, Settings } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useWs } from "@/providers/websocket-provider"
import { cn } from "@/lib/utils"

const NAV_ITEMS = [
  { href: "/", label: "Chat", icon: MessageSquare },
  { href: "/history", label: "History", icon: History },
  { href: "/memories", label: "Memories", icon: Brain },
  { href: "/settings", label: "Settings", icon: Settings },
] as const

export function Sidebar() {
  const pathname = usePathname()
  const { status } = useWs()

  return (
    <nav className="flex h-full w-56 shrink-0 flex-col border-r bg-card">
      <div className="px-4 py-5">
        <h1 className="text-base font-semibold tracking-tight">
          Home-Ops Agent
        </h1>
      </div>

      <div className="flex flex-1 flex-col gap-1 px-2">
        {NAV_ITEMS.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href)
          return (
            <Button
              key={item.href}
              variant="ghost"
              className={cn(
                "justify-start gap-2",
                isActive && "bg-muted text-foreground"
              )}
              render={<Link href={item.href} />}
            >
              <item.icon className="size-4" />
              {item.label}
            </Button>
          )
        })}
      </div>

      <div className="flex items-center gap-2 border-t px-4 py-3">
        <span
          className={cn(
            "size-2 rounded-full",
            status === "connected" && "bg-green-500",
            status === "connecting" && "bg-yellow-500",
            status === "disconnected" && "bg-muted-foreground"
          )}
        />
        <span className="text-xs text-muted-foreground">
          {status === "connected"
            ? "Connected"
            : status === "connecting"
              ? "Connecting..."
              : "Disconnected"}
        </span>
      </div>
    </nav>
  )
}
