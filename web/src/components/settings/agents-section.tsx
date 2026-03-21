'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import { AGENTS, MODEL_OPTIONS, PROMPT_DESCRIPTIONS } from '@/lib/constants';
import type { PromptsResponse } from '@/lib/types';
import { PromptPanel } from './prompt-panel';

interface AgentsSectionProps {
  prompts: PromptsResponse | null;
  models: Record<string, string>;
  onModelChange: (task: string, model: string) => void;
  onPromptSaved: () => void;
}

export function AgentsSection({
  prompts,
  models,
  onModelChange,
  onPromptSaved,
}: AgentsSectionProps) {
  const [editingPrompt, setEditingPrompt] = useState<string | null>(null);

  // Cluster context button
  const clusterContext = prompts?.cluster_context;
  const clusterContextCustomized = clusterContext?.is_customized ?? false;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          onClick={() => setEditingPrompt('cluster_context')}
        >
          Edit Cluster Context
        </Button>
        {clusterContextCustomized && (
          <Badge variant="outline">Customized</Badge>
        )}
      </div>

      <h3 className="mt-2 text-sm font-medium">Agents</h3>

      <div className="flex flex-col gap-4">
        {AGENTS.map((agent) => {
          const prompt = agent.promptKey ? prompts?.[agent.promptKey] : null;
          const isCustomized = prompt?.is_customized ?? false;

          return (
            <Card key={agent.modelKey}>
              <CardContent>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-medium">{agent.name}</span>
                    <p className="text-xs text-muted-foreground">
                      {agent.description}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {agent.promptKey && (
                      <Button
                        variant="outline"
                        onClick={() => setEditingPrompt(agent.promptKey!)}
                      >
                        Prompt{isCustomized ? ' *' : ''}
                      </Button>
                    )}
                    <Select
                      value={models[agent.modelKey] || 'claude-sonnet-4-6'}
                      onValueChange={(val) =>
                        onModelChange(agent.modelKey, val as string)
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {MODEL_OPTIONS.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {editingPrompt && prompts && (
        <PromptPanel
          promptKey={editingPrompt}
          prompt={prompts[editingPrompt]}
          label={
            AGENTS.find((a) => a.promptKey === editingPrompt)?.name ??
            (editingPrompt === 'cluster_context'
              ? 'Cluster Context'
              : editingPrompt)
          }
          description={PROMPT_DESCRIPTIONS[editingPrompt] ?? ''}
          onClose={() => setEditingPrompt(null)}
          onSaved={onPromptSaved}
        />
      )}
    </div>
  );
}
