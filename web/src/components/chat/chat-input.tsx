'use client';

import { useState, type KeyboardEvent } from 'react';
import { Send } from 'lucide-react';
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from '@/components/ui/input-group';

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState('');

  function handleSend() {
    const text = value.trim();
    if (!text) return;
    onSend(text);
    setValue('');
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="border-t px-6 py-3 lg:px-8">
      <InputGroup className="mx-auto h-auto max-w-5xl">
        <InputGroupTextarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="What would you like to know?"
          rows={4}
        />
        <InputGroupAddon align="inline-end">
          <InputGroupButton
            onClick={handleSend}
            disabled={disabled || !value.trim()}
            size="icon-sm"
            variant="default"
          >
            <Send className="size-4" />
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    </div>
  );
}
