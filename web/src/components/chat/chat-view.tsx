'use client';

import { useEffect, useState, useCallback, useDeferredValue } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '@/components/ui/button';
import { useWs } from '@/providers/websocket-provider';
import { fetchMessages } from '@/lib/api';
import { useSettings } from '@/hooks/use-settings';
import { ChatInput } from './chat-input';
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from '@/components/ai-elements/conversation';
import { Suggestions, Suggestion } from '@/components/ai-elements/suggestion';
import { Shimmer } from '@/components/ai-elements/shimmer';
import {
  ChainOfThought,
  ChainOfThoughtHeader,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from '@/components/ai-elements/chain-of-thought';
import { BotIcon, WrenchIcon } from 'lucide-react';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: { tool: string }[];
}

interface ActiveTool {
  tool: string;
  status: 'active' | 'complete';
}


export function ChatView() {
  const { send, subscribe, conversationId, setConversationId } = useWs();
  const { data: settings } = useSettings();
  const suggestions = settings?.chat_suggestions
    ? settings.chat_suggestions.split('|').map((s: string) => s.trim()).filter(Boolean)
    : [];
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([]);
  const deferredStreamingText = useDeferredValue(streamingText);

  // Load existing conversation on mount or when conversationId changes
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;

    fetchMessages(conversationId)
      .then((msgs) => {
        if (cancelled) return;
        const parsed: ChatMessage[] = [];
        for (const msg of msgs) {
          const content =
            typeof msg.content === 'string'
              ? msg.content
              : msg.content?.text || '';
          if (!content) continue;
          const toolCalls =
            typeof msg.content !== 'string'
              ? msg.content?.tool_calls
              : undefined;
          if (msg.role === 'user' || msg.role === 'assistant') {
            parsed.push({ role: msg.role, content, toolCalls });
          }
        }
        setMessages(parsed);
      })
      .catch(() => {
        if (cancelled) return;
        setConversationId(null);
        setMessages([]);
      });

    return () => {
      cancelled = true;
    };
  }, [conversationId, setConversationId]);

  // Handle incoming WebSocket messages via subscription
  useEffect(() => {
    return subscribe((msg) => {
      switch (msg.type) {
        case 'typing':
          setIsThinking(true);
          setStreamingText('');
          setActiveTools([]);
          break;

        case 'tool_start':
          setActiveTools((prev) => [
            ...prev,
            { tool: msg.tool!, status: 'active' },
          ]);
          break;

        case 'tool_end':
          setActiveTools((prev) =>
            prev.map((t, i) =>
              i === msg.tool_index ? { ...t, status: 'complete' } : t,
            ),
          );
          break;

        case 'stream_delta':
          setIsThinking(false);
          setIsStreaming(true);
          setStreamingText((prev) => prev + msg.delta);
          break;

        case 'stream_end':
          setIsStreaming(false);
          setIsThinking(false);
          setStreamingText('');
          setActiveTools([]);
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content: msg.content || '',
              toolCalls: msg.tool_calls,
            },
          ]);
          break;

        case 'message':
          setIsThinking(false);
          setIsStreaming(false);
          setStreamingText('');
          setActiveTools([]);
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content: msg.content || '',
              toolCalls: msg.tool_calls,
            },
          ]);
          break;

        case 'error':
          setIsThinking(false);
          setIsStreaming(false);
          setStreamingText('');
          setActiveTools([]);
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: msg.message || 'Error occurred' },
          ]);
          break;
      }
    });
  }, [subscribe]);

  const handleSend = useCallback(
    (text: string) => {
      setMessages((prev) => [...prev, { role: 'user', content: text }]);
      send({ message: text, conversation_id: conversationId });
      setIsThinking(true);
    },
    [send, conversationId],
  );

  const handleNewChat = useCallback(() => {
    setConversationId(null);
    setMessages([]);
    setIsThinking(false);
    setIsStreaming(false);
    setStreamingText('');
    setActiveTools([]);
  }, [setConversationId]);

  const isBusy = isThinking || isStreaming;

  return (
    <div className="relative flex h-full flex-col">
      <div className="absolute z-10 top-4 right-6">
        <Button variant="outline" size="lg" onClick={handleNewChat}>
          New Chat
        </Button>
      </div>

      <Conversation>
        <ConversationContent className="mx-auto max-w-5xl gap-4 px-4 lg:px-8">
          {messages.length === 0 && !isBusy ? (
            <ConversationEmptyState
              title="Home Ops Agent"
              description="Ask about your cluster, PRs, alerts, or anything else."
              icon={<BotIcon className="size-8" />}
            >
              <div className="space-y-4">
                <div className="space-y-1">
                  <BotIcon className="mx-auto size-8 text-muted-foreground" />
                  <h3 className="font-medium text-sm">Home Ops Agent</h3>
                  <p className="text-muted-foreground text-sm">
                    Ask about your cluster, PRs, alerts, or anything else.
                  </p>
                </div>
                {suggestions.length > 0 && (
                  <Suggestions className="justify-center">
                    {suggestions.map((s: string) => (
                      <Suggestion key={s} suggestion={s} onClick={handleSend} />
                    ))}
                  </Suggestions>
                )}
              </div>
            </ConversationEmptyState>
          ) : (
            <>
              {messages.map((msg, i) => (
                <MessageBubble key={i} message={msg} />
              ))}
              {/* Active tool calls */}
              {activeTools.length > 0 && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] rounded-lg bg-muted px-4 py-2.5">
                    <ChainOfThought defaultOpen={true}>
                      <ChainOfThoughtHeader>
                        Working... (
                        {activeTools.filter((t) => t.status === 'complete').length}/
                        {activeTools.length} tools)
                      </ChainOfThoughtHeader>
                      <ChainOfThoughtContent>
                        {activeTools.map((tc, i) => (
                          <ChainOfThoughtStep
                            key={i}
                            icon={WrenchIcon}
                            label={tc.tool}
                            status={tc.status === 'complete' ? 'complete' : 'active'}
                          />
                        ))}
                      </ChainOfThoughtContent>
                    </ChainOfThought>
                  </div>
                </div>
              )}

              {/* Streaming text response */}
              {isStreaming && deferredStreamingText && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] rounded-lg bg-muted px-4 py-2.5 text-sm text-foreground">
                    <div className="max-w-none text-sm [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-border [&_th]:bg-background/50 [&_th]:px-3 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium [&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-1.5 [&_pre]:my-2 [&_pre]:rounded-md [&_pre]:bg-background [&_pre]:p-3 [&_code]:rounded [&_code]:bg-background [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5 [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-medium [&_hr]:my-3 [&_hr]:border-border [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_a]:text-primary [&_a]:underline">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {deferredStreamingText}
                      </ReactMarkdown>
                    </div>
                  </div>
                </div>
              )}

              {/* Thinking indicator */}
              {isThinking && !isStreaming && activeTools.length === 0 && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] rounded-lg bg-muted px-4 py-2.5">
                    <Shimmer className="text-sm" duration={1.5}>
                      Thinking...
                    </Shimmer>
                  </div>
                </div>
              )}
            </>
          )}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      <ChatInput onSend={handleSend} disabled={isBusy} />
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2.5 text-sm ${
          isUser
            ? 'bg-primary text-primary-foreground whitespace-pre-wrap'
            : 'bg-muted text-foreground'
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
          <ToolCallsDisplay toolCalls={message.toolCalls} />
        )}
      </div>
    </div>
  );
}

function ToolCallsDisplay({ toolCalls }: { toolCalls: { tool: string }[] }) {
  return (
    <ChainOfThought className="mt-3 border-t border-border/50 pt-2">
      <ChainOfThoughtHeader>
        Tools used ({toolCalls.length})
      </ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {toolCalls.map((tc, i) => (
          <ChainOfThoughtStep
            key={i}
            icon={WrenchIcon}
            label={tc.tool}
            status="complete"
          />
        ))}
      </ChainOfThoughtContent>
    </ChainOfThought>
  );
}
