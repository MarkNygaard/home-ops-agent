import type {
  Settings,
  Skill,
  Conversation,
  Message,
  Memory,
  AgentTask,
  PromptsResponse,
  StatusResponse,
  AnalyticsResponse,
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

// Returns the response body: this endpoint reports validation problems as an
// `error` key with a 200, so discarding it made those failures invisible.
export function updateSetting(
  key: string,
  value: string,
): Promise<{ status?: string; key?: string; error?: string }> {
  return fetchJson("/api/settings/" + key, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  })
}

export function importOpenAITokens(body: {
  access_token: string
  refresh_token: string
  account_id: string
  expires_in?: number
}): Promise<{ status: string; expires_at?: string; error?: string }> {
  return fetchJson("/api/auth/openai", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

export function disconnectProvider(
  provider: string
): Promise<{ status: string; error?: string }> {
  return fetchJson("/api/auth/" + provider, { method: "DELETE" })
}

// --- ChatGPT sign-in (PKCE) ---
//
// These two report failures as a 4xx with a `detail` message written for the
// operator — "state mismatch", "PKCE verification failed" — so they cannot use
// fetchJson, which throws away the body and leaves only "400 Bad Request".

async function postWithDetail<T>(url: string, body?: unknown): Promise<T & { error?: string }> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let payload: Record<string, unknown> = {}
  try {
    payload = await res.json()
  } catch {
    // A proxy error page or an empty body; the status is all we have.
  }
  if (!res.ok) {
    const detail = payload.detail ?? payload.error
    return { error: typeof detail === "string" ? detail : `${res.status} ${res.statusText}` } as T & {
      error?: string
    }
  }
  return payload as T & { error?: string }
}

export function connectOpenAIStart(): Promise<{
  authorize_url?: string
  state?: string
  verifier?: string
  redirect_uri?: string
  error?: string
}> {
  return postWithDetail("/api/auth/openai/connect/start")
}

export function connectOpenAIComplete(body: {
  redirect: string
  state: string
  verifier: string
}): Promise<{ status?: string; account_id?: string; expires_at?: string; error?: string }> {
  return postWithDetail("/api/auth/openai/connect/complete", body)
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

export function createMemory(
  content: string,
  category: string,
): Promise<Memory & { error?: string }> {
  return fetch("/api/memories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, category }),
  }).then((r) => r.json())
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

// PR check
export function triggerPrCheck(): Promise<{
  status: string
  running_for_seconds?: number
}> {
  return fetchJson("/api/pr-check", { method: "POST" })
}

// Analytics
export function fetchAnalytics(days?: number): Promise<AnalyticsResponse> {
  const url = days !== undefined ? `/api/analytics?days=${days}` : "/api/analytics"
  return fetchJson<AnalyticsResponse>(url)
}
