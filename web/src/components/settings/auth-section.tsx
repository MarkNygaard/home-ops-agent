'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  disconnectProvider,
  importOpenAITokens,
  updateSetting,
} from '@/lib/api';
import type { Settings } from '@/lib/types';

interface AuthSectionProps {
  settings: Settings | null;
  onSaved: () => void;
}

export function AuthSection({ settings, onSaved }: AuthSectionProps) {
  const providers = settings?.providers;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h3 className="text-sm font-medium">Model Providers</h3>
        <p className="text-xs text-muted-foreground">
          Configure any combination of providers. Each model you assign to an
          agent is routed to its provider automatically.
        </p>
      </div>

      <ApiKeyCard
        title="Anthropic (Claude)"
        placeholder="sk-ant-..."
        settingKey="anthropic_api_key"
        status={providers?.anthropic}
        provider="anthropic"
        onSaved={onSaved}
      />

      <ApiKeyCard
        title="Kimi for Coding"
        placeholder="Kimi Code API key"
        settingKey="kimi_api_key"
        status={providers?.kimi}
        provider="kimi"
        helpText="API key from the Kimi Code Console (requires an active Kimi membership with Code benefits). Uses Kimi's Anthropic-compatible endpoint."
        onSaved={onSaved}
      />

      <OpenAICard status={providers?.openai} onSaved={onSaved} />
    </div>
  );
}

interface ApiKeyCardProps {
  title: string;
  placeholder: string;
  settingKey: string;
  provider: string;
  status?: { configured: boolean; hint?: string | null };
  helpText?: string;
  onSaved: () => void;
}

function ApiKeyCard({
  title,
  placeholder,
  settingKey,
  provider,
  status,
  helpText,
  onSaved,
}: ApiKeyCardProps) {
  const [value, setValue] = useState('');

  async function handleSave() {
    if (!value.trim()) return;
    await updateSetting(settingKey, value.trim());
    setValue('');
    onSaved();
  }

  async function handleDisconnect() {
    await disconnectProvider(provider);
    onSaved();
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{title}</span>
        <Badge variant={status?.configured ? 'default' : 'secondary'}>
          {status?.configured ? 'Configured' : 'Not set'}
        </Badge>
        {status?.hint && (
          <span className="font-mono text-xs text-muted-foreground">
            {status.hint}
          </span>
        )}
      </div>
      {helpText && <p className="text-xs text-muted-foreground">{helpText}</p>}
      <div className="flex gap-2">
        <Input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          className="max-w-sm"
        />
        <Button onClick={handleSave} variant="outline">
          Save
        </Button>
        {status?.configured && (
          <Button onClick={handleDisconnect} variant="ghost">
            Disconnect
          </Button>
        )}
      </div>
    </div>
  );
}

interface OpenAICardProps {
  status?: {
    configured: boolean;
    account_id?: string | null;
    expires_at?: string | null;
  };
  onSaved: () => void;
}

function OpenAICard({ status, onSaved }: OpenAICardProps) {
  const [accessToken, setAccessToken] = useState('');
  const [refreshToken, setRefreshToken] = useState('');
  const [accountId, setAccountId] = useState('');
  const [error, setError] = useState('');

  async function handleSave() {
    if (!accessToken.trim() || !refreshToken.trim() || !accountId.trim()) {
      setError('All three fields are required.');
      return;
    }
    const res = await importOpenAITokens({
      access_token: accessToken.trim(),
      refresh_token: refreshToken.trim(),
      account_id: accountId.trim(),
    });
    if (res.error) {
      setError(res.error);
      return;
    }
    setAccessToken('');
    setRefreshToken('');
    setAccountId('');
    setError('');
    onSaved();
  }

  async function handleDisconnect() {
    await disconnectProvider('openai');
    onSaved();
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">ChatGPT (OpenAI / Codex)</span>
        <Badge variant={status?.configured ? 'default' : 'secondary'}>
          {status?.configured ? 'Connected' : 'Not connected'}
        </Badge>
        {status?.expires_at && (
          <span className="text-xs text-muted-foreground">
            Token expires: {new Date(status.expires_at).toLocaleString()}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Authenticate locally with your ChatGPT subscription (e.g.{' '}
        <code>codex login</code>), then paste the tokens here. The server keeps
        them refreshed automatically.
      </p>
      <div className="flex max-w-sm flex-col gap-2">
        <Input
          type="password"
          value={accessToken}
          onChange={(e) => setAccessToken(e.target.value)}
          placeholder="access_token"
        />
        <Input
          type="password"
          value={refreshToken}
          onChange={(e) => setRefreshToken(e.target.value)}
          placeholder="refresh_token"
        />
        <Input
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          placeholder="chatgpt_account_id"
        />
        <div className="flex gap-2">
          <Button onClick={handleSave} variant="outline">
            Save Tokens
          </Button>
          {status?.configured && (
            <Button onClick={handleDisconnect} variant="ghost">
              Disconnect
            </Button>
          )}
        </div>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    </div>
  );
}
