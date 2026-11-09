import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Badge, type Tone } from '@/components/common/Badge';
import { cn } from '@/utils/cn';
import type { ApprovalStatus, RiskDecision } from '@/types/approvals';
import { STATUS_LABEL } from '@/utils/approvals';

const STATUS_TONE: Record<ApprovalStatus, Tone> = {
  PENDING: 'gold',
  UNDER_REVIEW: 'info',
  APPROVED: 'up',
  REJECTED: 'neutral',
  MODIFICATION_REQUESTED: 'info',
  EXPIRED: 'neutral',
  CANCELLED: 'neutral',
  INVALIDATED: 'warn',
  EXECUTION_PENDING: 'info',
  EXECUTION_SUBMITTED: 'info',
  EXECUTED: 'up',
  EXECUTION_FAILED: 'down',
};

export function StatusBadge({ status }: { status: ApprovalStatus }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{STATUS_LABEL[status] ?? status}</Badge>;
}

const RISK_TONE: Record<RiskDecision, Tone> = { PASS: 'up', REVIEW: 'warn', BLOCK: 'down', ERROR: 'down' };
const RISK_LABEL: Record<RiskDecision, string> = { PASS: 'Risk pass', REVIEW: 'Risk review', BLOCK: 'Risk block', ERROR: 'Risk error' };

export function RiskBadge({ decision }: { decision: RiskDecision }) {
  return <Badge tone={RISK_TONE[decision] ?? 'neutral'}>{RISK_LABEL[decision] ?? decision}</Badge>;
}

export function DirectionBadge({ direction }: { direction: string }) {
  return <Badge tone={direction === 'BUY' ? 'up' : 'down'}>{direction === 'BUY' ? 'Buy' : 'Sell'}</Badge>;
}

/** Time left until expiry, ticking each second. Shows "Expired" at zero; the server decides, this only displays. */
export function Countdown({ expiresAt, className }: { expiresAt: string; className?: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000));
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  const text = left === 0 ? 'Expired' : h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}:${String(s).padStart(2, '0')}`;
  return (
    <span
      className={cn('num tabular-nums', left === 0 ? 'text-neutral' : left < 120 ? 'font-semibold text-down-text' : left < 600 ? 'text-warn' : 'text-ink', className)}
      aria-label={left === 0 ? 'Expired' : `Expires in ${text}`}
      title={new Date(expiresAt).toISOString()}
    >
      {text}
    </span>
  );
}

/** Same shell as ConfirmDialog (native <dialog>), sized for forms. */
export function Modal({ open, title, onClose, children, wide }: { open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
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
        onClose();
      }}
      aria-label={title}
      className={cn(
        'max-h-[90vh] rounded-card border border-line-control bg-base-elevated p-0 text-ink shadow-float backdrop:bg-black/60',
        wide ? 'w-[min(94vw,640px)]' : 'w-[min(92vw,480px)]',
      )}
    >
      {open && (
        <div className="p-5">
          <h2 className="text-base font-semibold">{title}</h2>
          <div className="mt-3">{children}</div>
        </div>
      )}
    </dialog>
  );
}

/** A label/value row used in the confirmation summaries. */
export function Field({ label, children, strong }: { label: string; children: ReactNode; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line-soft py-1.5 last:border-0">
      <dt className="text-xs text-neutral">{label}</dt>
      <dd className={cn('num text-right text-sm', strong ? 'font-semibold text-ink' : 'text-ink')}>{children}</dd>
    </div>
  );
}

/** Lists server-side reasons (for example the failed approval checks) exactly as the backend reported them. */
export function ProblemList({ title, problems }: { title: string; problems: string[] }) {
  if (!problems.length) return null;
  return (
    <div role="alert" className="rounded-md border border-down/40 bg-down-faint p-3 text-xs text-down-text">
      <p className="font-semibold">{title}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        {problems.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  );
}
