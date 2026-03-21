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
      <div className="relative overflow-x-auto rounded-xl bg-card ring-1 ring-foreground/10">
        {/* Subtle grid background */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              "radial-gradient(circle, currentColor 1px, transparent 1px)",
            backgroundSize: "24px 24px",
          }}
        />

        <div className="relative flex items-center justify-center gap-0 px-8 py-10">
          {/* Agent node */}
          <div className="flex flex-col items-center gap-2.5">
            <div
              className="flex size-16 items-center justify-center rounded-full"
              style={{
                background:
                  "linear-gradient(135deg, var(--accent-orange-light), var(--accent-orange))",
                padding: "2px",
              }}
            >
              <div className="flex size-full items-center justify-center rounded-full bg-card">
                <IconRobot className="size-7 text-accent-orange" />
              </div>
            </div>
            <span className="text-xs font-semibold">Agent</span>
          </div>

          {/* Connecting line from agent */}
          <div className="mx-1 flex items-center sm:mx-2">
            <svg width="40" height="2" className="text-accent-orange/40">
              <line
                x1="0"
                y1="1"
                x2="40"
                y2="1"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeDasharray="4 3"
              />
            </svg>
            <svg
              width="8"
              height="10"
              viewBox="0 0 8 10"
              className="-ml-1 text-accent-orange/40"
            >
              <path d="M0 0 L8 5 L0 10" fill="currentColor" />
            </svg>
          </div>

          {/* Flow steps */}
          {steps.map((step, i) => (
            <div key={step.label} className="flex items-center">
              <div className="flex flex-col items-center gap-2.5">
                <span
                  className={cn(
                    "mb-0.5 text-[0.6rem] uppercase tracking-wider text-muted-foreground/60"
                  )}
                >
                  Step {i + 1}
                </span>
                <div className="flex size-12 items-center justify-center rounded-full bg-muted/50 ring-1 ring-foreground/10 transition-colors">
                  <step.icon className="size-5 text-muted-foreground" />
                </div>
                <span className="whitespace-nowrap text-[0.7rem] text-muted-foreground">
                  {step.label}
                </span>
              </div>

              {i < steps.length - 1 && (
                <div className="mx-1 flex items-center sm:mx-2">
                  <svg
                    width="32"
                    height="2"
                    className="text-muted-foreground/20"
                  >
                    <line
                      x1="0"
                      y1="1"
                      x2="32"
                      y2="1"
                      stroke="currentColor"
                      strokeWidth="1"
                      strokeDasharray="4 3"
                    />
                  </svg>
                  <svg
                    width="6"
                    height="8"
                    viewBox="0 0 6 8"
                    className="-ml-0.5 text-muted-foreground/20"
                  >
                    <path d="M0 0 L6 4 L0 8" fill="currentColor" />
                  </svg>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
