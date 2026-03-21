"use client"

import { SiteHeader } from "@/components/site-header"
import { ChatView } from "@/components/chat/chat-view"

export default function ChatPage() {
  return (
    <>
      <SiteHeader title="Chat" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <ChatView />
      </div>
    </>
  )
}
