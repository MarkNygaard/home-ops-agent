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

export const MODEL_OPTIONS = [
  { value: "claude-haiku-4-5", label: "Haiku 4.5" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6" },
  { value: "claude-opus-4-6", label: "Opus 4.6" },
] as const

export const CATEGORY_COLORS: Record<string, string> = {
  issue: "destructive",
  preference: "default",
  knowledge: "secondary",
  fix: "outline",
  config: "secondary",
  general: "secondary",
}

export const MODEL_MIGRATION: Record<string, string> = {
  "claude-sonnet-4-20250514": "claude-sonnet-4-6",
  "claude-opus-4-20250514": "claude-opus-4-6",
  "claude-sonnet-4-6-20250514": "claude-sonnet-4-6",
  "claude-opus-4-6-20250514": "claude-opus-4-6",
  "claude-haiku-4-5-20251001": "claude-haiku-4-5",
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
