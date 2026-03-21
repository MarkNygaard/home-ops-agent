"use client"

import { type ReactNode } from "react"
import { WebSocketProvider } from "@/providers/websocket-provider"
import { Sidebar } from "@/components/sidebar"

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <WebSocketProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex flex-1 flex-col overflow-hidden">
          {children}
        </main>
      </div>
    </WebSocketProvider>
  )
}
