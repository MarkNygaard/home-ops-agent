export const AGENTS = [
  {
    promptKey: "pr_review",
    modelKey: "pr_review",
    name: "PR Review",
    description:
      "Reviews open Renovate PRs. Reads diffs, checks CI status and labels, posts a review comment with risk assessment.",
  },
  {
    promptKey: null,
    modelKey: "alert_triage",
    name: "Alert Triage",
    description:
      "First responder for alerts from Alertmanager and Gatus. Checks pod status, reads logs, queries Prometheus metrics.",
  },
  {
    promptKey: "alert_response",
    modelKey: "alert_fix",
    name: "Alert Fix",
    description:
      "Takes corrective action when an issue is found. Can restart pods, reconcile Flux resources, and send enriched diagnostics via ntfy.",
  },
  {
    promptKey: null,
    modelKey: "code_fix",
    name: "Code Fix",
    description:
      "Writes code fixes for failing PRs. Understands Kubernetes manifests and HelmRelease schemas. Only modifies files under kubernetes/apps/.",
  },
  {
    promptKey: null,
    modelKey: "deep_review",
    name: "Deep Review",
    description:
      "Escalation agent for critical PRs. Re-reviews high-risk PRs with a more capable model. Used in Fully Autonomous mode.",
  },
  {
    promptKey: "chat",
    modelKey: "chat",
    name: "Chat",
    description:
      "Powers the interactive chat. Answers questions about cluster state, runs diagnostics on demand, and executes commands.",
  },
] as const

// Claude models are named by alias, not version: the Claude Code CLI resolves
// haiku/sonnet/opus to the current model, so this list does not need editing
// when a new one ships. That is why the metered Anthropic provider — which
// required pinning dated IDs — was removed.
export const MODEL_OPTIONS = [
  { value: "claude-code/haiku", label: "Haiku", provider: "claude_code" },
  { value: "claude-code/sonnet", label: "Sonnet", provider: "claude_code" },
  { value: "claude-code/opus", label: "Opus", provider: "claude_code" },
  { value: "kimi-for-coding", label: "Kimi for Coding", provider: "kimi" },
  // GPT-5.6 ships as three tiers. Terra is the everyday workhorse, Sol the
  // flagship, Luna the fast/cheap one. gpt-5.5 is kept because existing
  // settings may still name it; it is previous-generation.
  { value: "gpt-5.6-sol", label: "GPT-5.6 Sol", provider: "openai" },
  { value: "gpt-5.6-terra", label: "GPT-5.6 Terra", provider: "openai" },
  { value: "gpt-5.6-luna", label: "GPT-5.6 Luna", provider: "openai" },
  { value: "gpt-5.5", label: "GPT-5.5 (previous)", provider: "openai" },
] as const

export const PROVIDER_LABELS: Record<string, string> = {
  claude_code: "Claude subscription",
  kimi: "Kimi",
  openai: "OpenAI",
}

// Categories the agent's extractor uses, and that a hand-written memory may
// pick from. "issue" means a *recurring* pattern — entries in it are dropped
// from the system prompt after a week, because a one-off incident goes stale.
export const MEMORY_CATEGORIES = [
  { value: "knowledge", label: "Knowledge — how the cluster is built" },
  { value: "config", label: "Config — a setting and why" },
  { value: "fix", label: "Fix — a change that resolved something" },
  { value: "preference", label: "Preference — how the user wants things done" },
  { value: "issue", label: "Issue — a recurring problem (expires after 7 days)" },
  { value: "general", label: "General" },
] as const

// Mirrors LEVELS in workers/notifications.py. ATTENTION and FAILURE are never
// suppressed, whichever level is chosen.
export const NOTIFY_LEVELS = [
  {
    value: "all",
    label: "Everything",
    description:
      "Every step, including 'reviewed, looks safe' and 'fix pushed'. A routine merged PR sends two notifications.",
  },
  {
    value: "outcomes",
    label: "Outcomes only (recommended)",
    description:
      "Skips intermediate steps whose result is reported later. A routine merged PR sends one notification.",
  },
  {
    value: "actionable",
    label: "Only what needs me",
    description:
      "Reviews needing attention and anything that failed. Routine merges pass silently.",
  },
] as const

// Covers both vocabularies: the model-task keys used by api_usage and the
// agent_tasks.task_type enum, which overlap but are not identical.
export const TASK_LABELS: Record<string, string> = {
  pr_review: "PR Review",
  pr_merge: "PR Merge",
  deep_review: "Deep Review",
  alert_response: "Alert Response",
  alert_triage: "Alert Triage",
  alert_fix: "Alert Fix",
  code_fix: "Code Fix",
  cluster_fix: "Cluster Fix",
  user_chat: "Chat",
  chat: "Chat",
}

export const CATEGORY_COLORS: Record<string, string> = {
  issue: "destructive",
  preference: "default",
  knowledge: "secondary",
  fix: "outline",
  config: "secondary",
  general: "secondary",
}

// Settings written before the metered Anthropic provider was removed point at
// dated IDs. Map them onto the subscription aliases; the backend does the same
// for anything this list misses.
export const MODEL_MIGRATION: Record<string, string> = {
  "claude-haiku-4-5": "claude-code/haiku",
  "claude-sonnet-4-6": "claude-code/sonnet",
  "claude-opus-4-6": "claude-code/opus",
  "claude-opus-4-8": "claude-code/opus",
  "claude-sonnet-4-20250514": "claude-code/sonnet",
  "claude-opus-4-20250514": "claude-code/opus",
  "claude-sonnet-4-6-20250514": "claude-code/sonnet",
  "claude-opus-4-6-20250514": "claude-code/opus",
  "claude-haiku-4-5-20251001": "claude-code/haiku",
  // gpt-5.3-codex is deprecated for ChatGPT sign-in; gpt-5.4 and gpt-5.4-mini
  // retire from Codex on 2026-08-31. Terra is the recommended replacement for
  // the workhorse tiers, Luna for the mini one.
  "codex-5.3": "gpt-5.6-terra",
  "gpt-5.3-codex": "gpt-5.6-terra",
  "gpt-5.4": "gpt-5.6-terra",
  "gpt-5.4-mini": "gpt-5.6-luna",
}

export const PROMPT_DESCRIPTIONS: Record<string, string> = {
  cluster_context:
    "Shared context prepended to all agent prompts. Describe your cluster setup, IPs, domain, and infrastructure here.",
  pr_review:
    "Instructions for how the PR Review agent analyzes pull requests.",
  alert_response:
    "Instructions for how the Alert Fix agent investigates and resolves alerts.",
  chat: "Instructions for how the Chat agent responds to interactive questions.",
}
