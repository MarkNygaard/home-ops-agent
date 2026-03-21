"use client"

import { SiteHeader } from "@/components/site-header"
import { useSkills } from "@/hooks/use-skills"
import { SkillsSection } from "@/components/settings/skills-section"

export default function SettingsSkillsPage() {
  const { data: skills, mutate: mutateSkills } = useSkills()

  return (
    <>
      <SiteHeader title="Skills" />
      <div className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
        <div className="mx-auto max-w-3xl">
          <SkillsSection
            skills={skills ?? []}
            onUpdate={() => mutateSkills()}
          />
        </div>
      </div>
    </>
  )
}
