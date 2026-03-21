'use client';

import {
  IconGitPullRequest,
  IconAlertTriangle,
  IconBolt,
  IconCode,
  IconMessageChatbot,
} from '@tabler/icons-react';
import { Badge } from '@/components/ui/badge';
import { CardContent } from '@/components/ui/card';
import { useSettings } from '@/hooks/use-settings';
import { MODEL_MIGRATION } from '@/lib/constants';
import { cn } from '@/lib/utils';

const AGENT_DEFS = [
  {
    key: 'pr_review',
    name: 'PR Review',
    icon: IconGitPullRequest,
  },
  {
    key: 'alert_triage',
    name: 'Alert Triage',
    icon: IconAlertTriangle,
  },
  {
    key: 'alert_fix',
    name: 'Alert Fix',
    icon: IconBolt,
  },
  {
    key: 'code_fix',
    name: 'Code Fix',
    icon: IconCode,
  },
  {
    key: 'chat',
    name: 'Chat',
    icon: IconMessageChatbot,
  },
] as const;

interface AgentCardsProps {
  activeAgent: string;
  onSelect: (key: string) => void;
}

export function AgentCards({ activeAgent, onSelect }: AgentCardsProps) {
  const { data: settings } = useSettings();

  function getModelLabel(key: string): string {
    const raw = settings?.models?.[key];
    if (!raw) return 'Sonnet 4.6';
    const migrated = MODEL_MIGRATION[raw] || raw;
    if (migrated.includes('haiku')) return 'Haiku 4.5';
    if (migrated.includes('opus')) return 'Opus 4.6';
    return 'Sonnet 4.6';
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {AGENT_DEFS.map((agent) => {
        const isActive = activeAgent === agent.key;
        return (
          <div
            key={agent.key}
            className="cursor-pointer"
            onClick={() => onSelect(agent.key)}
          >
            <div
              className={cn(
                'rounded-xl p-0.5 transition-all',
                isActive
                  ? 'bg-linear-to-br from-accent-orange-light to-accent-orange'
                  : 'bg-border hover:bg-muted-foreground/20',
              )}
            >
              <div className="rounded-[10px] bg-card p-4">
                <CardContent className="flex flex-col items-center gap-3 pt-1 text-center">
                  <agent.icon
                    className={cn(
                      'size-6',
                      isActive ? 'text-accent-orange' : 'text-muted-foreground',
                    )}
                  />
                  <span className="text-sm font-medium">{agent.name}</span>
                  <Badge variant="accent" className="text-[0.65rem]">
                    {getModelLabel(agent.key)}
                  </Badge>
                </CardContent>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
