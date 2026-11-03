import { cn } from '@/utils/cn';

/** AURUM AI mark: an ascending "A" whose crossbar is a price trace. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={cn('h-8 w-8 shrink-0', className)} aria-hidden>
      <rect x="0" y="0" width="40" height="40" rx="10" fill="#0F4A3A" />
      <path d="M10 29 20 10l10 19" fill="none" stroke="#FFFFFF" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 23h5l2.5-3 2.5 3h4" fill="none" stroke="#C7F0A8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ collapsed }: { collapsed?: boolean }) {
  return (
    <span className="flex items-center gap-2.5" aria-label="AURUM AI">
      <LogoMark />
      {!collapsed && (
        <span className="flex items-baseline gap-1.5">
          <span className="text-lg font-bold tracking-tight text-ink">Aurum</span>
          <span className="text-lg font-bold tracking-tight text-gold">AI</span>
        </span>
      )}
    </span>
  );
}
