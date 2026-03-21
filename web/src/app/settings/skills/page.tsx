"use client"

import { SiteHeader } from "@/components/site-header"
import { useSkills } from "@/hooks/use-skills"
import { SkillsSection } from "@/components/settings/skills-section"

export default function SettingsSkillsPage() {
  const { data: skills, mutate: mutateSkills } = useSkills()

  return (
    <>
      <SiteHeader title="Skills" />
      <div className="flex-1 overflow-y-auto px-4 py-6 lg:px-8 lg:py-8">
        <div className="mx-auto max-w-6xl">
          <SkillsSection
            skills={skills ?? []}
            onUpdate={() => mutateSkills()}
          />
        </div>
      </div>
    </>
  )
}
