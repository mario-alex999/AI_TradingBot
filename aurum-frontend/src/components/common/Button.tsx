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

// One filled primary per screen, inverted white on the dark ground; the gold
// accent is kept for data and selection, never spent on button fills.
const variants: Record<Variant, string> = {
  primary: 'bg-ink text-[#121215] font-semibold hover:bg-white active:bg-[#d9d9db]',
  secondary: 'border border-line-control text-ink hover:border-[#55555b] hover:bg-base-elevated active:bg-base-raised',
  ghost: 'text-ink-muted hover:bg-base-elevated hover:text-ink active:bg-base-raised',
  danger: 'border border-down/50 text-down-text hover:bg-[#2c1a20]',
};

const sizes: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-8 px-3.5 text-sm gap-2',
  icon: 'h-8 w-8 justify-center',
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
