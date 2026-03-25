"use client"

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react"
import { useWebSocket, type WsStatus, type WsMessageHandler } from "@/hooks/use-websocket"

interface WebSocketContextValue {
  status: WsStatus
  send: (data: unknown) => void
  subscribe: (handler: WsMessageHandler) => () => void
  conversationId: number | null
  setConversationId: (id: number | null) => void
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null)

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const { status, send, subscribe } = useWebSocket()
  const [conversationId, setConversationIdState] = useState<number | null>(null)

  // Initialize from localStorage
  useEffect(() => {
    const stored = localStorage.getItem("conversationId")
    if (stored) setConversationIdState(parseInt(stored, 10))
  }, [])

  // Track conversation ID from incoming messages
  useEffect(() => {
    return subscribe((msg) => {
      if (msg.conversation_id) {
        setConversationIdState(msg.conversation_id)
        localStorage.setItem("conversationId", String(msg.conversation_id))
      }
    })
  }, [subscribe])

  const setConversationId = useCallback((id: number | null) => {
    setConversationIdState(id)
    if (id === null) {
      localStorage.removeItem("conversationId")
    } else {
      localStorage.setItem("conversationId", String(id))
    }
  }, [])

  return (
    <WebSocketContext.Provider
      value={{ status, send, subscribe, conversationId, setConversationId }}
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
