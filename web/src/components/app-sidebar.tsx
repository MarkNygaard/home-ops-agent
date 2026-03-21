"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  IconMessageChatbot,
  IconHistory,
  IconBrain,
  IconSettings,
  IconCircuitGround,
} from "@tabler/icons-react"

import { useWs } from "@/providers/websocket-provider"
import { cn } from "@/lib/utils"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarGroup,
  SidebarGroupContent,
} from "@/components/ui/sidebar"

const NAV_ITEMS = [
  { href: "/", label: "Chat", icon: IconMessageChatbot },
  { href: "/history", label: "History", icon: IconHistory },
  { href: "/memories", label: "Memories", icon: IconBrain },
  { href: "/settings", label: "Settings", icon: IconSettings },
] as const

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { status } = useWs()

  return (
    <Sidebar collapsible="offcanvas" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              render={<Link href="/" />}
              className="data-[slot=sidebar-menu-button]:p-1.5!"
            >
              <IconCircuitGround className="size-5!" />
              <span className="text-base font-semibold">Home-Ops Agent</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV_ITEMS.map((item) => {
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      render={<Link href={item.href} />}
                      isActive={isActive}
                    >
                      <item.icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton className="cursor-default hover:bg-transparent">
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
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
