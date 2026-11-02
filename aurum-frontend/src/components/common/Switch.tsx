import { cn } from '@/utils/cn';

interface Props {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
  disabledReason?: string;
}

export function Switch({ checked, onChange, label, description, disabled, disabledReason }: Props) {
  return (
    <label
      className={cn('flex items-start justify-between gap-4', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer')}
      title={disabled ? disabledReason : undefined}
    >
      <span className="min-w-0">
        <span className="block text-sm text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-neutral">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-0.5 inline-flex h-5 w-9 shrink-0 rounded-full border transition-colors',
          checked ? 'border-gold/60 bg-gold/80' : 'border-line bg-base-elevated',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 h-3.5 w-3.5 rounded-full bg-ink transition-transform',
            checked ? 'translate-x-[18px]' : 'translate-x-0.5',
          )}
        />
      </button>
    </label>
  );
}
