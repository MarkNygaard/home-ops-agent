"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useWs } from "@/providers/websocket-provider"
import { fetchMessages } from "@/lib/api"
import type { WsMessage } from "@/lib/types"
import { ChatInput } from "./chat-input"

interface ChatMessage {
  role: "user" | "assistant"
  content: string
  toolCalls?: { tool: string }[]
}

export function ChatView() {
  const { lastMessage, send, conversationId, setConversationId } = useWs()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isThinking, setIsThinking] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const processedRef = useRef<WsMessage | null>(null)

  // Load existing conversation on mount or when conversationId changes
  const loadConversation = useCallback(async () => {
    if (!conversationId) return
    try {
      const msgs = await fetchMessages(conversationId)
      const parsed: ChatMessage[] = []
      for (const msg of msgs) {
        const content =
          typeof msg.content === "string"
            ? msg.content
            : msg.content?.text || ""
        if (!content) continue
        const toolCalls =
          typeof msg.content !== "string"
            ? msg.content?.tool_calls
            : undefined
        if (msg.role === "user" || msg.role === "assistant") {
          parsed.push({ role: msg.role, content, toolCalls })
        }
      }
      setMessages(parsed)
    } catch {
      // Conversation may have been deleted
      setConversationId(null)
      setMessages([])
    }
  }, [conversationId, setConversationId])

  useEffect(() => {
    loadConversation()
  }, [loadConversation])

  // Handle incoming WebSocket messages
  useEffect(() => {
    if (!lastMessage || lastMessage === processedRef.current) return
    processedRef.current = lastMessage

    if (lastMessage.type === "typing") {
      setIsThinking(true)
    } else if (lastMessage.type === "message") {
      setIsThinking(false)
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: lastMessage.content || "",
          toolCalls: lastMessage.tool_calls,
        },
      ])
    } else if (lastMessage.type === "error") {
      setIsThinking(false)
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: lastMessage.message || "Error occurred" },
      ])
    }
  }, [lastMessage])

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, isThinking])

  function handleSend(text: string) {
    setMessages((prev) => [...prev, { role: "user", content: text }])
    send({ message: text, conversation_id: conversationId })
    setIsThinking(true)
  }

  function handleNewChat() {
    setConversationId(null)
    setMessages([])
    setIsThinking(false)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-end px-4 py-2 lg:px-6">
        <Button variant="outline" size="sm" onClick={handleNewChat}>
          New Chat
        </Button>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          {messages.map((msg, i) => (
            <MessageBubble key={i} message={msg} />
          ))}
          {isThinking && (
            <div className="text-sm text-muted-foreground">Thinking...</div>
          )}
        </div>
      </div>

      <ChatInput onSend={handleSend} disabled={isThinking} />
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user"
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2.5 text-sm ${
          isUser
            ? "bg-primary text-primary-foreground whitespace-pre-wrap"
            : "bg-muted text-foreground"
        }`}
      >
        {isUser ? (
          message.content
        ) : (
          <div className="max-w-none text-sm [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-border [&_th]:bg-background/50 [&_th]:px-3 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium [&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-1.5 [&_pre]:my-2 [&_pre]:rounded-md [&_pre]:bg-background [&_pre]:p-3 [&_code]:rounded [&_code]:bg-background [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5 [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-medium [&_hr]:my-3 [&_hr]:border-border [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_a]:text-primary [&_a]:underline">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        )}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {message.toolCalls.map((tc, i) => (
              <Badge key={i} variant="secondary">
                {tc.tool}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
