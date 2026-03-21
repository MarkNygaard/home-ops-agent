'use client';

import { useState, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { SiteHeader } from '@/components/site-header';
import { useSettings } from '@/hooks/use-settings';
import { usePrompts } from '@/hooks/use-prompts';
import { updateSetting } from '@/lib/api';
import { MODEL_MIGRATION } from '@/lib/constants';
import { AgentsSection } from '@/components/settings/agents-section';

export default function SettingsAgentsPage() {
  const { data: settings, mutate: mutateSettings } = useSettings();
  const { data: prompts, mutate: mutatePrompts } = usePrompts();

  const [dirty, setDirty] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [modelOverrides, setModelOverrides] = useState<Record<string, string>>(
    {},
  );

  // Derive models from settings with local overrides
  const models = useMemo(() => {
    const base: Record<string, string> = {};
    if (settings?.models) {
      for (const [task, model] of Object.entries(settings.models)) {
        base[task] = MODEL_MIGRATION[model] || model;
      }
    }
    return { ...base, ...modelOverrides };
  }, [settings, modelOverrides]);

  function showStatus(msg: string) {
    setStatusMsg(msg);
    setTimeout(() => setStatusMsg(''), 3000);
  }

  async function handleSave() {
    const modelTasks = [
      'pr_review',
      'alert_triage',
      'alert_fix',
      'code_fix',
      'chat',
    ];
    const saves = modelTasks
      .filter((task) => models[task])
      .map((task) => updateSetting(`model_${task}`, models[task]));

    await Promise.all(saves);
    setDirty(false);
    setModelOverrides({});
    showStatus('Models saved');
    mutateSettings();
  }

  return (
    <>
      <SiteHeader title="Agents" />
      <div className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
        <div className="mx-auto flex max-w-4xl flex-col gap-6">
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              Configure models and system prompts for each agent task. The
              cluster context prompt is shared across all agents.
            </p>
            <AgentsSection
              prompts={prompts ?? null}
              models={models}
              onModelChange={(task, model) => {
                setModelOverrides((prev) => ({ ...prev, [task]: model }));
                setDirty(true);
              }}
              onPromptSaved={() => {
                mutatePrompts();
                showStatus('Prompt saved');
              }}
            />
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} size="lg" disabled={!dirty}>
              Save Models
            </Button>
            {statusMsg && (
              <span className="text-sm text-green-500">{statusMsg}</span>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
