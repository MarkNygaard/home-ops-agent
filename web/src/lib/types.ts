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
  type: "typing" | "message" | "error"
  conversation_id?: number
  content?: string
  message?: string
  tool_calls?: { tool: string }[]
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
}
