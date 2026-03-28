// --- API response types ---

export interface Settings {
  agent_enabled: boolean
  pr_mode: string
  auth_method: string
  has_api_key: boolean
  api_key_hint: string | null
  oauth_status: string
  oauth_token_expires: string | null
  models: Record<string, string>
  alert_cooldown_seconds: number
  ntfy_topics: string
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

export interface StatusResponse {
  has_credentials: boolean
  last_pr_check_at: string | null
}

export interface CostByModel {
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  requests: number
}

export interface CostByTask {
  task_type: string
  cost_usd: number
  requests: number
}

export interface CostsResponse {
  days: number
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
  total_requests: number
  by_model: CostByModel[]
  by_task: CostByTask[]
  pricing: Record<string, { input: number; output: number }>
}
