"use client"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { updateSkill as apiUpdateSkill } from "@/lib/api"
import type { Skill } from "@/lib/types"

interface SkillsSectionProps {
  skills: Skill[]
  onUpdate: () => void
}

export function SkillsSection({ skills, onUpdate }: SkillsSectionProps) {
  async function handleToggle(skill: Skill, checked: boolean) {
    await apiUpdateSkill(skill.id, { enabled: checked })
    onUpdate()
  }

  async function handleConfigChange(
    skill: Skill,
    configKey: string,
    value: string
  ) {
    const config = { ...skill.config, [configKey]: value }
    await apiUpdateSkill(skill.id, { config })
    onUpdate()
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium">Skills</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Skills are groups of tools the agent can use. Built-in skills are
          always enabled.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        {skills.map((skill) => (
          <Card key={skill.id} size="sm">
            <CardContent>
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{skill.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {skill.tool_count} tool
                      {skill.tool_count !== 1 ? "s" : ""}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {skill.description}
                  </p>
                </div>

                {skill.builtin ? (
                  <Badge variant="secondary" className="shrink-0">
                    Always On
                  </Badge>
                ) : (
                  <div className="flex shrink-0 items-center gap-2">
                    <Label className="text-xs text-muted-foreground">
                      {skill.enabled ? "Enabled" : "Disabled"}
                    </Label>
                    <Switch
                      checked={skill.enabled}
                      onCheckedChange={(checked) =>
                        handleToggle(skill, checked)
                      }
                      size="sm"
                    />
                  </div>
                )}
              </div>

              {skill.config_fields &&
                skill.config_fields.length > 0 &&
                (skill.builtin || skill.enabled) && (
                  <div className="mt-3 flex flex-col gap-2">
                    {skill.config_fields.map((field) => (
                      <div key={field.key} className="flex flex-col gap-1">
                        <Label className="text-xs">{field.label}</Label>
                        <Input
                          type={field.type === "url" ? "text" : field.type}
                          defaultValue={
                            skill.config[field.key] || field.default || ""
                          }
                          placeholder={field.default || ""}
                          onBlur={(e) =>
                            handleConfigChange(
                              skill,
                              field.key,
                              e.target.value
                            )
                          }
                        />
                      </div>
                    ))}
                  </div>
                )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
