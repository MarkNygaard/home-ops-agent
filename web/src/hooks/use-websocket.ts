"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import type { WsMessage } from "@/lib/types"

export type WsStatus = "connecting" | "connected" | "disconnected"

export function useWebSocket() {
  const [status, setStatus] = useState<WsStatus>("disconnected")
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connect = useCallback(() => {
    if (typeof window === "undefined") return

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`)
    wsRef.current = ws
    setStatus("connecting")

    ws.onopen = () => {
      setStatus("connected")
    }

    ws.onclose = () => {
      setStatus("disconnected")
      wsRef.current = null
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      setStatus("disconnected")
    }

    ws.onmessage = (event) => {
      try {
        const data: WsMessage = JSON.parse(event.data)
        setLastMessage(data)
      } catch {
        // ignore malformed messages
      }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [connect])

  const send = useCallback((data: unknown) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  return { status, lastMessage, send }
}
