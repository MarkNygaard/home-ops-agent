"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  IconLayoutDashboard,
  IconMessageChatbot,
  IconHistory,
  IconBrain,
  IconSettings,
  IconCircuitGround,
  IconChevronRight,
  IconShieldCog,
  IconPuzzle,
  IconRobot,
  IconKey,
  IconMessage,
  IconCurrencyDollar,
} from "@tabler/icons-react"

import { useWs } from "@/providers/websocket-provider"
import { cn } from "@/lib/utils"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarGroup,
  SidebarGroupContent,
} from "@/components/ui/sidebar"

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: IconLayoutDashboard },
  { href: "/chat", label: "Chat", icon: IconMessageChatbot },
  { href: "/history", label: "History", icon: IconHistory },
  { href: "/memories", label: "Memories", icon: IconBrain },
] as const

const SETTINGS_ITEMS = [
  { href: "/settings", label: "General", icon: IconShieldCog },
  { href: "/settings/skills", label: "Skills", icon: IconPuzzle },
  { href: "/settings/agents", label: "Agents", icon: IconRobot },
  { href: "/settings/chat", label: "Chat", icon: IconMessage },
  { href: "/settings/costs", label: "Costs", icon: IconCurrencyDollar },
  { href: "/settings/auth", label: "Authentication", icon: IconKey },
] as const

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const pathname = usePathname()
  const { status } = useWs()
  const isSettingsActive = pathname.startsWith("/settings")
  const [settingsOpen, setSettingsOpen] = useState(isSettingsActive)

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

              <Collapsible open={settingsOpen} onOpenChange={setSettingsOpen} className="group/collapsible">
                <SidebarMenuItem>
                  <CollapsibleTrigger render={<SidebarMenuButton />}>
                    <IconSettings />
                    <span>Settings</span>
                    <IconChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <SidebarMenuSub>
                      {SETTINGS_ITEMS.map((item) => {
                        const isActive = item.href === "/settings"
                          ? pathname === "/settings"
                          : pathname === item.href
                        return (
                          <SidebarMenuSubItem key={item.href}>
                            <SidebarMenuSubButton
                              render={<Link href={item.href} />}
                              isActive={isActive}
                            >
                              <item.icon />
                              <span>{item.label}</span>
                            </SidebarMenuSubButton>
                          </SidebarMenuSubItem>
                        )
                      })}
                    </SidebarMenuSub>
                  </CollapsibleContent>
                </SidebarMenuItem>
              </Collapsible>
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
