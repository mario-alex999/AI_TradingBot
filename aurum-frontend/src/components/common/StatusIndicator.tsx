import { cn } from '@/utils/cn';
import { STATUS_META, type Status } from '@/utils/status';

export function StatusDot({ status, className }: { status: Status; className?: string }) {
  const m = STATUS_META[status];
  return (
    <span className={cn('relative inline-flex h-2 w-2 shrink-0', className)} aria-hidden>
      {m.pulse && <span className={cn('absolute inset-0 animate-ping rounded-full opacity-60', m.dot)} />}
      <span className={cn('relative inline-flex h-2 w-2 rounded-full', m.dot)} />
    </span>
  );
}

export function StatusIndicator({ status, label, className }: { status: Status; label?: string; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs text-ink-muted', className)}>
      <StatusDot status={status} />
      <span>{label ?? STATUS_META[status].label}</span>
    </span>
  );
}
