import { cn } from '@/utils/cn';

interface Props<T extends string> {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: Props<T>) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-5 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          type="button"
          id={`tab-${t.id}`}
          aria-selected={t.id === value}
          aria-controls={`panel-${t.id}`}
          onClick={() => onChange(t.id)}
          className={cn(
            '-mb-px whitespace-nowrap border-b px-1 py-2.5 text-sm transition-colors',
            t.id === value ? 'border-gold font-medium text-ink' : 'border-transparent text-neutral hover:text-ink',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
