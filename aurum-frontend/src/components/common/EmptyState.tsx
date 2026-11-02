import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';
import { cn } from '@/utils/cn';

interface Props {
  title: string;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
  compact?: boolean;
}

export function EmptyState({ title, description, icon, action, className, compact }: Props) {
  return (
    <div className={cn('flex flex-col items-center justify-center text-center', compact ? 'gap-1.5 py-6' : 'gap-3 py-12', className)}>
      <div className="flex h-10 w-10 items-center justify-center rounded-full border border-line bg-base-elevated text-neutral">
        {icon ?? <Inbox className="h-5 w-5" aria-hidden />}
      </div>
      <div>
        <p className="text-sm font-medium text-ink">{title}</p>
        {description && <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-neutral">{description}</p>}
      </div>
      {action}
    </div>
  );
}
