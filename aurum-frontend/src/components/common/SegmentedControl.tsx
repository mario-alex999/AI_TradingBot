import { cn } from '@/utils/cn';

interface Props<T extends string> {
  value: T;
  options: readonly T[] | { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
  className?: string;
}

export function SegmentedControl<T extends string>({ value, options, onChange, label, className }: Props<T>) {
  const opts = (options as readonly (T | { value: T; label: string })[]).map((o) =>
    typeof o === 'string' ? { value: o, label: o } : o,
  );
  return (
    <div role="radiogroup" aria-label={label} className={cn('inline-flex rounded-md border border-line bg-base-raised p-0.5', className)}>
      {opts.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              'num h-7 rounded px-2.5 text-xs font-medium transition-colors',
              active ? 'bg-gold-faint text-gold' : 'text-ink-muted hover:text-ink',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
