import type { ReactNode } from 'react';

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {/* The section ("Intelligence", "Trading") is already in the sidebar; no eyebrow above the title. */}
        <h1 className="text-2xl font-semibold tracking-[-0.01em] text-ink" data-section={eyebrow}>
          {title}
        </h1>
        {description && <p className="mt-1.5 max-w-3xl text-sm text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
