"use client"

import {
  IconRobot,
  IconGitPullRequest,
  IconFileSearch,
  IconFileText,
  IconNotes,
  IconMessage,
  IconAlertTriangle,
  IconBox,
  IconFileAnalytics,
  IconChartLine,
  IconBolt,
  IconBell,
  IconCode,
  IconGitBranch,
  IconTestPipe,
  IconSend,
  IconMessageChatbot,
  IconSearch,
  IconTerminal2,
  IconReport,
  IconArrowRight,
} from "@tabler/icons-react"
import { cn } from "@/lib/utils"

type FlowStep = {
  icon: typeof IconRobot
  label: string
}

const FLOWS: Record<string, FlowStep[]> = {
  pr_review: [
    { icon: IconGitPullRequest, label: "Trigger" },
    { icon: IconFileSearch, label: "Check PR" },
    { icon: IconFileText, label: "Read Diff" },
    { icon: IconNotes, label: "Release Notes" },
    { icon: IconMessage, label: "Post Review" },
  ],
  alert_triage: [
    { icon: IconAlertTriangle, label: "Receive Alert" },
    { icon: IconBox, label: "Check Pods" },
    { icon: IconFileAnalytics, label: "Read Logs" },
    { icon: IconChartLine, label: "Query Metrics" },
    { icon: IconBell, label: "Notify" },
  ],
  alert_fix: [
    { icon: IconAlertTriangle, label: "Receive Alert" },
    { icon: IconFileAnalytics, label: "Diagnose" },
    { icon: IconBolt, label: "Apply Fix" },
    { icon: IconBox, label: "Verify" },
    { icon: IconBell, label: "Notify" },
  ],
  code_fix: [
    { icon: IconCode, label: "Detect Issue" },
    { icon: IconGitBranch, label: "Create Branch" },
    { icon: IconFileText, label: "Write Fix" },
    { icon: IconTestPipe, label: "Validate" },
    { icon: IconSend, label: "Open PR" },
  ],
  chat: [
    { icon: IconMessageChatbot, label: "User Message" },
    { icon: IconSearch, label: "Gather Context" },
    { icon: IconTerminal2, label: "Run Tools" },
    { icon: IconReport, label: "Analyze" },
    { icon: IconMessage, label: "Respond" },
  ],
}

interface AgentFlowProps {
  activeAgent: string
}

export function AgentFlow({ activeAgent }: AgentFlowProps) {
  const steps = FLOWS[activeAgent] ?? FLOWS.chat

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-sm font-medium text-muted-foreground">
        Agent Workflow
      </h2>
      <div className="flex items-center justify-center gap-2 overflow-x-auto rounded-xl bg-card px-6 py-6 ring-1 ring-foreground/10 sm:gap-3">
        {/* Center robot icon */}
        <div className="flex flex-col items-center gap-1.5">
          <div className="flex size-12 items-center justify-center rounded-full ring-2 ring-accent-orange">
            <IconRobot className="size-6 text-accent-orange" />
          </div>
          <span className="text-xs font-medium">Agent</span>
        </div>

        <IconArrowRight className="size-4 shrink-0 text-muted-foreground" />

        {/* Flow steps */}
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-center gap-2 sm:gap-3">
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={cn(
                  "flex size-10 items-center justify-center rounded-full ring-1",
                  "ring-foreground/10 text-muted-foreground"
                )}
              >
                <step.icon className="size-5" />
              </div>
              <span className="whitespace-nowrap text-[0.65rem] text-muted-foreground">
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <IconArrowRight className="size-3.5 shrink-0 text-muted-foreground/50" />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
