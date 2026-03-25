"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import type { WsMessage } from "@/lib/types"

export type WsStatus = "connecting" | "connected" | "disconnected"
export type WsMessageHandler = (message: WsMessage) => void

export function useWebSocket() {
  const [status, setStatus] = useState<WsStatus>("disconnected")
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const listenersRef = useRef<Set<WsMessageHandler>>(new Set())

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
        for (const handler of listenersRef.current) {
          handler(data)
        }
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

  const subscribe = useCallback((handler: WsMessageHandler) => {
    listenersRef.current.add(handler)
    return () => { listenersRef.current.delete(handler) }
  }, [])

  return { status, send, subscribe }
}
