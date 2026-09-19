// --- API response types ---

export interface ProviderStatus {
  configured: boolean
  hint?: string | null
  account_id?: string | null
  expires_at?: string | null
}

export interface Settings {
  agent_enabled: boolean
  thinking_level?: string
  pr_mode: string
  alert_mode: string
  providers: {
    claude_code: ProviderStatus
    kimi: ProviderStatus
    openai: ProviderStatus
  }
  models: Record<string, string>
  alert_cooldown_seconds: number
  ntfy_topics: string
  notify_level?: string
  ntfy_url?: string
  ntfy_agent_topic?: string
  ntfy_token_configured?: boolean
  ntfy_token_hint?: string | null
  pr_check_interval_seconds: number
  chat_suggestions: string
}

export interface ConfigField {
  key: string
  label: string
  type: string
  default: string
}

export interface Skill {
  id: string
  name: string
  description: string
  enabled: boolean
  builtin: boolean
  tool_count: number
  config_fields: ConfigField[]
  config: Record<string, string>
}

export interface Conversation {
  id: number
  title: string
  source: string
  status: string
  created_at: string
}

export interface MessageContent {
  text?: string
  tool_calls?: { tool: string }[]
}

export interface Message {
  role: "user" | "assistant" | "tool_use" | "tool_result"
  content: MessageContent | string
}

export interface Memory {
  id: number
  content: string
  category: string
  created_at: string
  source_conversation_id: number | null
}

export interface AgentTask {
  id: number
  type: string
  trigger: string
  summary: string | null
  status: string
  created_at: string
  messages?: Message[]
}

export interface PromptInfo {
  default: string
  custom: string | null
  is_customized: boolean
}

export interface PromptsResponse {
  [key: string]: PromptInfo
}

export interface WsMessage {
  type:
    | "typing"
    | "message"
    | "error"
    | "tool_start"
    | "tool_end"
    | "stream_delta"
    | "stream_end"
    | "thinking_delta"
    | "thinking_withheld"
  conversation_id?: number
  content?: string
  message?: string
  tool_calls?: { tool: string }[]
  tokens?: number
  // Streaming fields
  delta?: string
  tool?: string
  tool_index?: number
}

export interface HistoryItem {
  type: string
  id: number
  trigger: string
  created_at: string
  summary: string | null
  is_conversation: boolean
}

export interface PrCheckResult {
  status: string
  at?: string
  open_prs?: number
  reviewed?: number
  failed?: number
  rate_limited?: boolean
  error?: string
  skipped?: number
  merged?: number
}

/** Where a live run has got to, so the workflow diagram can show it moving.
 *  Null when nothing is running — which is what makes the diagram fall back to
 *  showing the shape of the flow rather than a stale highlight. */
export interface RunProgress {
  run_id: string
  agent: string
  step: string
  detail: string
  started_at: string
  updated_at: string
}

export interface WorkerHealth {
  name: string
  alive: boolean
  restarts: number
  started_at: string
  last_error: string | null
  last_death_at: string | null
}

export interface StatusResponse {
  has_credentials: boolean
  workers?: WorkerHealth[]
  last_pr_check_at: string | null
  pr_check_running?: boolean
  last_pr_check_result?: PrCheckResult | null
  run?: RunProgress | null
}

export interface UsageByModel {
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
  requests: number
}

export interface UsageByTask {
  task_type: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
  requests: number
}

export interface RunsByType {
  task_type: string
  completed: number
  failed: number
  total: number
}

export interface AnalyticsResponse {
  days: number
  // False while every provider is plan-billed; the UI hides cost entirely.
  is_billed: boolean
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  total_requests: number
  by_model: UsageByModel[]
  by_task: UsageByTask[]
  runs: RunsByType[]
  total_runs: number
  total_failed: number
  pricing: Record<string, { input: number; output: number }>
}

export interface WriteRecord {
  id: number
  tool: string
  target: string
  source: string
  outcome: "ok" | "blocked" | "error"
  detail: string
  conversation_id: number | null
  created_at: string | null
}

export interface AuditResponse {
  days: number
  writes: WriteRecord[]
  counts: { ok: number; blocked: number; error: number; total: number }
  sources: string[]
  tracked_tools: string[]
}
