import { useId, useState, type ReactNode } from 'react';
import { Info } from 'lucide-react';
import { cn } from '@/utils/cn';

/** A small "i" that explains a term on hover, focus or tap. Accessible via aria-describedby. */
export function InfoTip({ children, label = 'More information', className }: { children: ReactNode; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className={cn('relative inline-flex align-middle', className)}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="flex h-5 w-5 items-center justify-center rounded-full text-neutral transition hover:bg-base-raised hover:text-ink"
      >
        <Info className="h-3.5 w-3.5" aria-hidden />
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute bottom-full left-1/2 z-30 mb-2 w-64 -translate-x-1/2 rounded-lg bg-ink px-3 py-2 text-xs font-normal leading-relaxed text-white shadow-float"
        >
          {children}
        </span>
      )}
    </span>
  );
}
