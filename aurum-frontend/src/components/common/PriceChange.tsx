import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { cn } from '@/utils/cn';
import { directionTone, formatPercent } from '@/utils/format';

export function PriceChange({ value, className }: { value: number | null | undefined; className?: string }) {
  const tone = directionTone(value);
  const Icon = tone === 'up' ? ArrowUpRight : tone === 'down' ? ArrowDownRight : Minus;
  return (
    <span
      className={cn(
        'num inline-flex items-center gap-0.5 font-medium',
        tone === 'up' && 'text-up',
        tone === 'down' && 'text-down-text',
        tone === 'flat' && 'text-neutral',
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {formatPercent(value, 2, true)}
    </span>
  );
}
