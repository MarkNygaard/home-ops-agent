"use client"

import { useState } from "react"
import { SiteHeader } from "@/components/site-header"
import { StatusBar } from "@/components/dashboard/status-bar"
import { AgentCards } from "@/components/dashboard/agent-cards"
import { AgentFlow } from "@/components/dashboard/agent-flow"
import { RecentActivity } from "@/components/dashboard/recent-activity"
import { SkillsOverview } from "@/components/dashboard/skills-overview"

export default function DashboardPage() {
  const [activeAgent, setActiveAgent] = useState("pr_review")

  return (
    <>
      <SiteHeader title="Dashboard" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-8">
          <StatusBar />
          <AgentCards activeAgent={activeAgent} onSelect={setActiveAgent} />
          <AgentFlow activeAgent={activeAgent} />
          <RecentActivity />
          <SkillsOverview />
        </div>
      </div>
    </>
  )
}
