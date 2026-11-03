import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';

export type Tone = 'neutral' | 'gold' | 'up' | 'down' | 'warn' | 'info';

// Pale tint with text of the same hue; status colour only, never decoration.
const tones: Record<Tone, string> = {
  neutral: 'bg-base-elevated text-ink-muted',
  gold: 'bg-warn-faint text-warn',
  up: 'bg-[#16251f] text-up',
  down: 'bg-[#2c1a20] text-down-text',
  warn: 'bg-warn-faint text-warn',
  info: 'bg-info-faint text-info',
};

export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex flex-none items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
