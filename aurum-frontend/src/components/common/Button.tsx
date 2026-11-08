import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/utils/cn';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'icon';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
  /** Explains why the button is disabled; shown as a tooltip and to screen readers. */
  disabledReason?: string;
}

// Pill buttons: a near-black filled primary and an outlined secondary, as in the template.
const variants: Record<Variant, string> = {
  primary: 'rounded-full bg-ink text-inverse font-medium hover:bg-ink/90 active:bg-ink/80',
  secondary: 'rounded-full border border-line-control bg-base-card text-ink hover:border-ink/40 active:bg-base-raised',
  ghost: 'rounded-full text-ink-muted hover:bg-base-raised hover:text-ink active:bg-line-soft',
  danger: 'rounded-full border border-down/50 bg-base-card text-down-text hover:bg-down-faint',
};

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3.5 text-xs gap-1.5',
  md: 'h-9 px-5 text-sm gap-2',
  icon: 'h-9 w-9 justify-center',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, disabledReason, className, children, disabled, type = 'button', ...rest },
  ref,
) {
  const isDisabled = disabled || loading;
  return (
    <button
      ref={ref}
      type={type}
      disabled={isDisabled}
      title={isDisabled && disabledReason ? disabledReason : rest.title}
      aria-description={isDisabled && disabledReason ? disabledReason : undefined}
      className={cn(
        'inline-flex shrink-0 items-center font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45',
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});
