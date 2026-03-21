'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { updateSetting } from '@/lib/api';
import type { Settings } from '@/lib/types';

interface AuthSectionProps {
  settings: Settings | null;
  authMethod: string;
  onAuthMethodChange: (method: string) => void;
  onSaved: () => void;
}

export function AuthSection({
  settings,
  authMethod,
  onAuthMethodChange,
  onSaved,
}: AuthSectionProps) {
  const [apiKey, setApiKey] = useState('');

  async function handleSaveApiKey() {
    if (!apiKey.trim()) return;
    await updateSetting('anthropic_api_key', apiKey.trim());
    setApiKey('');
    onSaved();
  }

  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-medium">Claude Authentication</h3>

      <div className="flex gap-4">
        <Label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="auth_method"
            value="api_key"
            checked={authMethod === 'api_key'}
            onChange={(e) => onAuthMethodChange(e.target.value)}
          />
          API Key
        </Label>
        <Label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="auth_method"
            value="oauth"
            checked={authMethod === 'oauth'}
            onChange={(e) => onAuthMethodChange(e.target.value)}
          />
          OAuth (Max/Pro)
        </Label>
      </div>

      {authMethod === 'api_key' && (
        <div className="flex flex-col gap-2">
          {settings && (
            <div className="flex items-center gap-2">
              <Badge variant={settings.has_api_key ? 'default' : 'secondary'}>
                {settings.has_api_key ? 'Active' : 'Not set'}
              </Badge>
              {settings.api_key_hint && (
                <span className="font-mono text-xs text-muted-foreground">
                  {settings.api_key_hint}
                </span>
              )}
            </div>
          )}
          <div className="flex gap-2">
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-ant-..."
              className="max-w-sm"
            />
            <Button onClick={handleSaveApiKey} variant="outline">
              Save API Key
            </Button>
          </div>
        </div>
      )}

      {authMethod === 'oauth' && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Badge
              variant={
                settings?.oauth_status === 'active' ? 'default' : 'secondary'
              }
            >
              {settings?.oauth_status ?? 'Not configured'}
            </Badge>
            {settings?.oauth_token_expires && (
              <span className="text-xs text-muted-foreground">
                Expires:{' '}
                {new Date(settings.oauth_token_expires).toLocaleString()}
              </span>
            )}
          </div>
          <Button
            variant="outline"
            className="w-fit"
            render={<a href="/auth/login" />}
          >
            Authorize with Anthropic
          </Button>
          <p className="text-xs text-muted-foreground">
            OAuth client credentials are configured via the SOPS secret at
            deploy time.
          </p>
        </div>
      )}
    </div>
  );
}
