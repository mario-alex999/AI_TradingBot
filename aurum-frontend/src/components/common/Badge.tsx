import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';

export type Tone = 'neutral' | 'gold' | 'up' | 'down' | 'warn' | 'info';

const tones: Record<Tone, string> = {
  neutral: 'border-line bg-base-elevated text-ink-muted',
  gold: 'border-gold/40 bg-gold-faint text-gold',
  up: 'border-up/30 bg-up-faint text-up',
  down: 'border-down/30 bg-down-faint text-down-text',
  warn: 'border-warn/30 bg-warn-faint text-warn',
  info: 'border-info/30 bg-info-faint text-info',
};

export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
