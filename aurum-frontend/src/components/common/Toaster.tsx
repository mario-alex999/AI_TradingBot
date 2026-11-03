import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';
import { useToastStore, type ToastTone } from '@/stores/toastStore';
import { cn } from '@/utils/cn';

const ICON: Record<ToastTone, typeof Info> = { info: Info, success: CheckCircle2, warning: TriangleAlert, error: AlertCircle };
const COLOR: Record<ToastTone, string> = { info: 'text-info', success: 'text-up', warning: 'text-warn', error: 'text-down-text' };

export function Toaster() {
  const { toasts, dismiss } = useToastStore();
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(92vw,360px)] flex-col gap-2">
      {toasts.map((t) => {
        const Icon = ICON[t.tone];
        return (
          <div key={t.id} role="status" className="pointer-events-auto flex animate-fade-in gap-3 rounded-card border border-line-control bg-base-elevated p-3 shadow-float">
            <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', COLOR[t.tone])} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{t.title}</p>
              {t.description && <p className="mt-0.5 text-xs text-ink-muted">{t.description}</p>}
            </div>
            <button type="button" aria-label="Dismiss notification" className="text-neutral hover:text-ink" onClick={() => dismiss(t.id)}>
              <X className="h-4 w-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
