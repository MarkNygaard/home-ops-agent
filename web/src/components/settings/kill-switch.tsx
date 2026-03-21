'use client';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { updateSetting, fetchSettings } from '@/lib/api';
import type { Settings } from '@/lib/types';

interface KillSwitchProps {
  settings: Settings | null;
  onToggle: () => void;
}

export function KillSwitch({ settings, onToggle }: KillSwitchProps) {
  const enabled = settings?.agent_enabled ?? true;

  async function handleToggle() {
    const current = await fetchSettings();
    await updateSetting(
      'agent_enabled',
      current.agent_enabled ? 'false' : 'true',
    );
    onToggle();
  }

  return (
    <Card>
      <CardContent>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium">Agent</span>
            <Badge variant={enabled ? 'default' : 'secondary'}>
              {enabled ? 'Enabled' : 'Disabled'}
            </Badge>
          </div>
          <Button
            variant={enabled ? 'destructive' : 'default'}
            onClick={handleToggle}
          >
            {enabled ? 'Disable Agent' : 'Enable Agent'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
