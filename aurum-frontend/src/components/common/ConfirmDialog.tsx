import { useEffect, useRef, type ReactNode } from 'react';
import { Button } from './Button';

interface Props {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  tone?: 'primary' | 'danger';
  onConfirm: () => void;
  onCancel: () => void;
}

/** Native <dialog>-based confirmation for important configuration changes. */
export function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', tone = 'primary', onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === 'function') d.showModal();
      else d.setAttribute('open', '');
    } else if (!open && d.open) {
      if (typeof d.close === 'function') d.close();
      else d.removeAttribute('open');
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      aria-labelledby="confirm-title"
      className="w-[min(92vw,440px)] rounded-card border border-line bg-base-card p-0 text-ink shadow-2xl backdrop:bg-black/60"
    >
      {open && (
        <div className="p-5">
          <h2 id="confirm-title" className="text-base font-semibold">
            {title}
          </h2>
          <div className="mt-2 text-sm text-ink-muted">{description}</div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button variant={tone === 'danger' ? 'danger' : 'primary'} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </div>
        </div>
      )}
    </dialog>
  );
}
