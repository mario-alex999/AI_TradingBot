import { useId, type SelectHTMLAttributes } from 'react';
import { cn } from '@/utils/cn';

interface Props extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'onChange'> {
  label: string;
  hideLabel?: boolean;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}

export function Select({ label, hideLabel, options, onChange, className, id, ...rest }: Props) {
  const auto = useId();
  const sid = id ?? auto;
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <label htmlFor={sid} className={hideLabel ? 'sr-only' : 'text-xs font-medium text-ink-muted'}>
        {label}
      </label>
      <select id={sid} className="field pr-8" onChange={(e) => onChange(e.target.value)} {...rest}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
