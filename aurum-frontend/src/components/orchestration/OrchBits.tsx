import { useEffect, useState } from 'react';
import { Badge, type Tone } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/approvals/ApprovalBits';
import { describeError } from '@/utils/errors';
import type { ExecutionStatus, HealthStatus, WorkflowState } from '@/types/orchestration';

const EXEC_TONE: Record<ExecutionStatus, Tone> = {
  RECEIVED: 'neutral',
  VALIDATING: 'neutral',
  QUEUED: 'gold',
  DISPATCHED: 'info',
  RUNNING: 'info',
  WAITING_CALLBACK: 'info',
  SUCCEEDED: 'up',
  FAILED: 'down',
  TIMED_OUT: 'warn',
  RETRY_PENDING: 'warn',
  DEAD_LETTERED: 'down',
  CANCELLED: 'neutral',
};
const EXEC_LABEL: Record<ExecutionStatus, string> = {
  RECEIVED: 'Received',
  VALIDATING: 'Validating',
  QUEUED: 'Queued',
  DISPATCHED: 'Sent to n8n',
  RUNNING: 'Running',
  WAITING_CALLBACK: 'Waiting for n8n',
  SUCCEEDED: 'Succeeded',
  FAILED: 'Failed',
  TIMED_OUT: 'Timed out',
  RETRY_PENDING: 'Retry pending',
  DEAD_LETTERED: 'Dead letter',
  CANCELLED: 'Cancelled',
};

export function ExecStatusBadge({ status }: { status: ExecutionStatus }) {
  return <Badge tone={EXEC_TONE[status] ?? 'neutral'}>{EXEC_LABEL[status] ?? status}</Badge>;
}

const WF_TONE: Record<WorkflowState, Tone> = {
  REGISTERED: 'neutral',
  VALIDATED: 'info',
  ACTIVE: 'up',
  PAUSED: 'gold',
  DEGRADED: 'warn',
  DISABLED: 'neutral',
  RETIRED: 'neutral',
};

export function WorkflowStateBadge({ state, placeholder }: { state: WorkflowState; placeholder?: boolean }) {
  if (placeholder) return <Badge tone="neutral">Reserved (not built)</Badge>;
  return <Badge tone={WF_TONE[state] ?? 'neutral'}>{state.charAt(0) + state.slice(1).toLowerCase()}</Badge>;
}

const H_TONE: Record<HealthStatus, Tone> = { HEALTHY: 'up', DEGRADED: 'warn', UNAVAILABLE: 'down', UNKNOWN: 'neutral' };
export function HealthBadge({ status }: { status: HealthStatus }) {
  return <Badge tone={H_TONE[status] ?? 'neutral'}>{status.charAt(0) + status.slice(1).toLowerCase()}</Badge>;
}

/**
 * Every operator action asks for a reason; it is stored in the Module 08 audit log against the signed-in person.
 * The server may still refuse (for example a business failure is never retried) and its message is shown as is.
 */
export function ReasonDialog({
  open,
  title,
  description,
  confirmLabel,
  danger,
  extra,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  extra?: { label: string; hint: string };
  pending: boolean;
  error: unknown;
  onClose: () => void;
  onSubmit: (reason: string, extraChecked: boolean) => void;
}) {
  const [reason, setReason] = useState('');
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (open) {
      setReason('');
      setChecked(false);
    }
  }, [open]);
  return (
    <Modal open={open} title={title} onClose={onClose}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (reason.trim()) onSubmit(reason.trim(), checked);
        }}
      >
        <p className="text-sm text-ink-muted">{description}</p>
        <label className="flex flex-col gap-1 text-xs text-ink-muted" htmlFor="orch-reason">
          Reason (recorded in the audit log)
          <textarea id="orch-reason" className="field min-h-[72px] text-sm" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} required />
        </label>
        {extra && (
          <label className="flex items-start gap-2 text-xs text-ink">
            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} className="mt-0.5" />
            <span>
              {extra.label}
              <span className="block text-ink-muted">{extra.hint}</span>
            </span>
          </label>
        )}
        {!!error && (
          <p role="alert" className="rounded-md border border-down/40 bg-down-faint p-2 text-xs text-down-text">
            {describeError(error).description}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button type="submit" size="sm" variant={danger ? 'danger' : 'primary'} loading={pending} disabled={!reason.trim()} disabledReason="Give a reason first">
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
