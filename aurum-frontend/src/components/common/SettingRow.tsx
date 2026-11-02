import type { ReactNode } from 'react';

export function SettingRow({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-3 border-b border-line-soft py-4 last:border-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] md:gap-8">
      <div>
        <p className="text-sm font-medium text-ink">{title}</p>
        {description && <p className="mt-0.5 text-xs text-neutral">{description}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
