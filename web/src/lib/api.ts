import type {
  Settings,
  Skill,
  Conversation,
  Message,
  Memory,
  AgentTask,
  PromptsResponse,
  StatusResponse,
} from "./types"

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Settings
export function fetchSettings(): Promise<Settings> {
  return fetchJson<Settings>("/api/settings")
}

export function updateSetting(key: string, value: string): Promise<void> {
  return fetchJson("/api/settings/" + key, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  })
}

// Skills
export function fetchSkills(): Promise<Skill[]> {
  return fetchJson<Skill[]>("/api/skills")
}

export function updateSkill(
  id: string,
  data: { enabled?: boolean; config?: Record<string, string> }
): Promise<{ error?: string }> {
  return fetchJson("/api/skills/" + id, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
}

// Conversations
export function fetchConversations(limit?: number): Promise<Conversation[]> {
  const url =
    limit !== undefined
      ? `/api/conversations?limit=${limit}`
      : "/api/conversations"
  return fetchJson<Conversation[]>(url)
}

export function fetchMessages(id: number): Promise<Message[]> {
  return fetchJson<Message[]>(`/api/conversations/${id}/messages`)
}

export function deleteConversation(id: number): Promise<void> {
  return fetch(`/api/conversations/${id}`, { method: "DELETE" }).then(() => {})
}

// History (agent tasks)
export function fetchHistory(
  taskType?: string,
  limit?: number
): Promise<AgentTask[]> {
  const params = new URLSearchParams()
  if (taskType) params.set("task_type", taskType)
  if (limit !== undefined) params.set("limit", String(limit))
  const qs = params.toString()
  return fetchJson<AgentTask[]>("/api/history" + (qs ? "?" + qs : ""))
}

export function fetchTaskDetail(id: number): Promise<AgentTask> {
  return fetchJson<AgentTask>(`/api/history/${id}`)
}

// Memories
export function fetchMemories(category?: string): Promise<Memory[]> {
  const url = category
    ? `/api/memories?category=${encodeURIComponent(category)}`
    : "/api/memories"
  return fetchJson<Memory[]>(url)
}

export function deleteMemory(id: number): Promise<void> {
  return fetch(`/api/memories/${id}`, { method: "DELETE" }).then(() => {})
}

// Prompts
export function fetchPrompts(): Promise<PromptsResponse> {
  return fetchJson<PromptsResponse>("/api/prompts")
}

export function resetPrompt(name: string): Promise<void> {
  return fetch(`/api/prompts/${name}`, { method: "DELETE" }).then(() => {})
}

// Status
export function fetchStatus(): Promise<StatusResponse> {
  return fetchJson<StatusResponse>("/api/status")
}
