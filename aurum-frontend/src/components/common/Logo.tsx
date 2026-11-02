import { cn } from '@/utils/cn';

/** AURUM AI mark: an ascending "A" whose crossbar is a price trace. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={cn('h-8 w-8 shrink-0', className)} aria-hidden>
      <rect x="1" y="1" width="38" height="38" rx="8" fill="#080D16" stroke="#D6AD60" strokeWidth="1.5" />
      <path d="M10 29 20 10l10 19" fill="none" stroke="#D6AD60" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 23h5l2.5-3 2.5 3h4" fill="none" stroke="#F1F5F9" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ collapsed }: { collapsed?: boolean }) {
  return (
    <span className="flex items-center gap-2.5" aria-label="AURUM AI">
      <LogoMark />
      {!collapsed && (
        <span className="flex items-baseline gap-1.5">
          <span className="text-[15px] font-semibold tracking-[0.22em] text-ink">AURUM</span>
          <span className="font-mono text-sm font-semibold text-gold">AI</span>
        </span>
      )}
    </span>
  );
}
