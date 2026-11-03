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
          'relative mt-0.5 inline-flex h-[18px] w-8 shrink-0 rounded-full border transition-colors',
          checked ? 'border-gold bg-gold' : 'border-line-control bg-line',
        )}
      >
        <span
          className={cn(
            'absolute top-px h-3.5 w-3.5 rounded-full transition-transform',
            checked ? 'translate-x-[15px] bg-white' : 'translate-x-px bg-white'
          )}
        />
      </button>
    </label>
  );
}
