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

const variants: Record<Variant, string> = {
  primary: 'bg-gold text-base font-semibold hover:bg-[#e0bb73] disabled:bg-gold/40',
  secondary: 'border border-line bg-base-elevated text-ink hover:border-[#36486a] hover:bg-[#1e2d45]',
  ghost: 'text-ink-muted hover:bg-base-elevated hover:text-ink',
  danger: 'border border-down/40 bg-down-faint text-down-text hover:bg-down/20',
};

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-9 px-4 text-sm gap-2',
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
        'inline-flex shrink-0 items-center rounded-md font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50',
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
