"use client"

import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { useSkills } from "@/hooks/use-skills"

export function SkillsOverview() {
  const { data: skills } = useSkills()

  const enabledSkills = (skills ?? []).filter(
    (s) => s.builtin || s.enabled
  )

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">Skills</h2>
        <Button variant="link" size="sm" className="text-xs" render={<Link href="/settings/skills" />}>
          Manage
        </Button>
      </div>

      {enabledSkills.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          No skills configured.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {enabledSkills.map((skill) => (
            <Card key={skill.id} size="sm" className="border-accent-orange/20">
              <CardContent className="flex flex-col gap-2">
                <span className="text-sm font-medium">{skill.name}</span>
                <Badge variant="secondary" className="w-fit text-[0.65rem]">
                  {skill.tool_count} tool{skill.tool_count !== 1 ? "s" : ""}
                </Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
