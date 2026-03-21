"use client"

import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react"
import { useWebSocket, type WsStatus } from "@/hooks/use-websocket"
import type { WsMessage } from "@/lib/types"

interface WebSocketContextValue {
  status: WsStatus
  lastMessage: WsMessage | null
  send: (data: unknown) => void
  conversationId: number | null
  setConversationId: (id: number | null) => void
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null)

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const { status, lastMessage, send } = useWebSocket()
  const [conversationId, setConversationIdState] = useState<number | null>(null)

  // Initialize from localStorage
  useEffect(() => {
    const stored = localStorage.getItem("conversationId")
    if (stored) setConversationIdState(parseInt(stored, 10))
  }, [])

  // Track conversation ID from incoming messages
  useEffect(() => {
    if (lastMessage?.conversation_id) {
      setConversationIdState(lastMessage.conversation_id)
      localStorage.setItem(
        "conversationId",
        String(lastMessage.conversation_id)
      )
    }
  }, [lastMessage])

  function setConversationId(id: number | null) {
    setConversationIdState(id)
    if (id === null) {
      localStorage.removeItem("conversationId")
    } else {
      localStorage.setItem("conversationId", String(id))
    }
  }

  return (
    <WebSocketContext.Provider
      value={{ status, lastMessage, send, conversationId, setConversationId }}
    >
      {children}
    </WebSocketContext.Provider>
  )
}

export function useWs() {
  const ctx = useContext(WebSocketContext)
  if (!ctx) throw new Error("useWs must be used within WebSocketProvider")
  return ctx
}
