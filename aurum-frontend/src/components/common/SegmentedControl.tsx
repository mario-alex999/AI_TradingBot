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
    <div role="radiogroup" aria-label={label} className={cn('inline-flex items-center gap-3 border-b border-line', className)}>
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
              'num h-8 px-0.5 text-xs font-medium transition-colors',
              active ? 'selected-line' : 'text-neutral hover:text-ink',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
