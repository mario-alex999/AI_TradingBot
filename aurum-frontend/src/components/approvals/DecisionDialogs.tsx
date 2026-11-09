import { useMemo, useState } from 'react';
import { AlertTriangle, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { formatCurrency, formatNumber, formatPercent } from '@/utils/format';
import { newRequestId } from '@/services/approvalService';
import type { ApprovalRequest, ApproveBody, ModifyBody, RejectBody } from '@/types/approvals';
import { Countdown, Field, Modal, ProblemList } from './ApprovalBits';
import { errorProblems, priceDigits } from '@/utils/approvals';

const str = (v: number | null | undefined) => (v === null || v === undefined ? null : String(v));

/**
 * Final confirmation. It shows exactly what will be approved and sends those same values back; the backend refuses
 * the approval if anything changed in the meantime (parameters, size, risk, evidence or request version).
 */
export function ApproveDialog({
  open,
  request,
  evidenceHash,
  onClose,
  onSubmit,
  pending,
  error,
}: {
  open: boolean;
  request: ApprovalRequest;
  evidenceHash: string | null;
  onClose: () => void;
  onSubmit: (b: ApproveBody) => void;
  pending: boolean;
  error: unknown;
}) {
  const [acks, setAcks] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [comment, setComment] = useState('');
  // One id per opening of the dialog: a retried click after a network error is applied once by the server.
  const clientRequestId = useMemo(() => (open ? newRequestId() : ''), [open]);
  const d = priceDigits(request.symbol);
  const warnings = request.riskWarnings ?? [];
  const allAcked = warnings.every((w) => acks.includes(w.ruleId));
  const reason = !evidenceHash
    ? 'Open and review the evidence first'
    : evidenceHash !== request.evidenceHash
      ? 'The evidence changed: review the current evidence'
      : !allAcked
        ? 'Acknowledge every risk warning'
        : !confirmed
          ? 'Tick the confirmation'
          : undefined;

  const submit = () =>
    onSubmit({
      clientRequestId,
      requestVersion: request.version,
      evidenceHash: evidenceHash ?? '',
      acknowledgedWarnings: acks,
      comment: comment.trim() || undefined,
      confirmation: {
        symbol: request.symbol,
        direction: request.direction,
        entry: String(request.entry),
        stopLoss: String(request.stopLoss),
        takeProfit: String(request.takeProfit),
        positionSize: str(request.positionSize),
        maxRiskAmount: str(request.riskAmount),
      },
    });

  return (
    <Modal open={open} title={`Approve ${request.direction === 'BUY' ? 'buy' : 'sell'} ${request.symbol}?`} onClose={onClose} wide>
      <p className="text-sm text-ink-muted">
        Approving sends an execution request to Module 09, which checks the risk again before any order. Expires in{' '}
        <Countdown expiresAt={request.expiresAt} />.
      </p>
      <dl className="mt-3 rounded-md border border-line bg-base-card px-3 py-1">
        <Field label="Instrument" strong>{request.symbol}</Field>
        <Field label="Direction" strong>{request.direction}</Field>
        <Field label="Entry">{formatNumber(request.entry, d)}</Field>
        <Field label="Stop-loss">{formatNumber(request.stopLoss, d)}</Field>
        <Field label="Take-profit">{formatNumber(request.takeProfit, d)}</Field>
        <Field label="Position size (lots)" strong>{request.positionSize == null ? 'Not sized' : formatNumber(request.positionSize, 2)}</Field>
        <Field label="Maximum loss at stop" strong>{formatCurrency(request.riskAmount)} ({formatPercent(request.riskPct)})</Field>
        <Field label="Risk decision (Module 06)">{request.riskDecision}</Field>
      </dl>
      {warnings.length > 0 && (
        <fieldset className="mt-3 rounded-md border border-warn/40 bg-warn-faint p-3">
          <legend className="flex items-center gap-1 px-1 text-xs font-semibold text-warn">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Acknowledge each risk warning
          </legend>
          {warnings.map((w) => (
            <label key={w.ruleId} className="mt-1.5 flex items-start gap-2 text-xs text-ink">
              <input
                type="checkbox"
                className="mt-0.5 accent-gold"
                checked={acks.includes(w.ruleId)}
                onChange={(e) => setAcks((a) => (e.target.checked ? [...a, w.ruleId] : a.filter((x) => x !== w.ruleId)))}
              />
              <span>
                <span className="num font-semibold">{w.ruleId}</span> {w.message}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <label htmlFor="approve-comment" className="mt-3 block text-xs font-medium text-ink-muted">
        Comment (optional)
      </label>
      <textarea id="approve-comment" className="field mt-1 h-16 w-full" maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
      <label className="mt-3 flex items-start gap-2 text-sm text-ink">
        <input type="checkbox" className="mt-1 accent-gold" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        <span>I have reviewed the evidence and risk, and I approve this trade with the parameters shown above.</span>
      </label>
      {!!error && <div className="mt-3"><ProblemList title="The trade was not approved" problems={errorProblems(error)} /></div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" icon={<ShieldCheck className="h-4 w-4" aria-hidden />} disabled={!!reason || pending} disabledReason={reason} loading={pending} onClick={submit}>
          Approve trade
        </Button>
      </div>
    </Modal>
  );
}

const REASON_LABEL: Record<string, string> = {
  trader_discretion: 'Trader discretion',
  risk_too_high: 'Risk too high',
  weak_evidence: 'Weak evidence',
  poor_timing: 'Poor timing',
  news_risk: 'News risk',
  market_conditions_changed: 'Market conditions changed',
  other: 'Other (explain)',
};

export function RejectDialog({
  open,
  request,
  reasons,
  onClose,
  onSubmit,
  pending,
  error,
}: {
  open: boolean;
  request: ApprovalRequest;
  reasons: string[];
  onClose: () => void;
  onSubmit: (b: RejectBody) => void;
  pending: boolean;
  error: unknown;
}) {
  const [reasonCode, setReasonCode] = useState('');
  const [comment, setComment] = useState('');
  const clientRequestId = useMemo(() => (open ? newRequestId() : ''), [open]);
  const why = !reasonCode ? 'Choose a reason' : reasonCode === 'other' && !comment.trim() ? 'Explain the reason' : undefined;
  return (
    <Modal open={open} title={`Reject ${request.symbol} ${request.direction}`} onClose={onClose}>
      <label htmlFor="reject-reason" className="text-xs font-medium text-ink-muted">
        Reason
      </label>
      <select id="reject-reason" className="field mt-1 w-full" value={reasonCode} onChange={(e) => setReasonCode(e.target.value)}>
        <option value="">Choose…</option>
        {reasons.map((r) => (
          <option key={r} value={r}>
            {REASON_LABEL[r] ?? r.replace(/_/g, ' ')}
          </option>
        ))}
      </select>
      <label htmlFor="reject-comment" className="mt-3 block text-xs font-medium text-ink-muted">
        Comment {reasonCode === 'other' ? '(required)' : '(optional)'}
      </label>
      <textarea id="reject-comment" className="field mt-1 h-20 w-full" maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
      <p className="mt-2 text-xs text-neutral">The reason is recorded in the audit trail and sent back to the decision engine.</p>
      {!!error && <div className="mt-3"><ProblemList title="Not rejected" problems={errorProblems(error)} /></div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="danger"
          disabled={!!why || pending}
          disabledReason={why}
          loading={pending}
          onClick={() => onSubmit({ clientRequestId, requestVersion: request.version, reasonCode, comment: comment.trim() || undefined })}
        >
          Reject
        </Button>
      </div>
    </Modal>
  );
}

const KINDS: { id: string; label: string; field?: 'entry' | 'stopLoss' | 'takeProfit' | 'positionSize' }[] = [
  { id: 'change_entry', label: 'Change entry', field: 'entry' },
  { id: 'change_stop_loss', label: 'Change stop-loss', field: 'stopLoss' },
  { id: 'change_take_profit', label: 'Change take-profit', field: 'takeProfit' },
  { id: 'change_position_size', label: 'Change position size', field: 'positionSize' },
  { id: 'request_additional_analysis', label: 'More analysis' },
  { id: 'request_additional_risk_assessment', label: 'Another risk assessment' },
  { id: 'request_updated_market_information', label: 'Updated market information' },
];

/** Asks for changes. Parameter changes create a new version of the idea (the original is kept) with a fresh risk
 * assessment and a new approval request; nothing is changed silently. */
export function ModifyDialog({
  open,
  request,
  onClose,
  onSubmit,
  pending,
  error,
}: {
  open: boolean;
  request: ApprovalRequest;
  onClose: () => void;
  onSubmit: (b: ModifyBody) => void;
  pending: boolean;
  error: unknown;
}) {
  const [kinds, setKinds] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [comment, setComment] = useState('');
  const clientRequestId = useMemo(() => (open ? newRequestId() : ''), [open]);
  const changes = Object.fromEntries(
    KINDS.filter((k) => k.field && kinds.includes(k.id) && values[k.field]?.trim()).map((k) => [k.field as string, (values[k.field as string] ?? '').trim()]),
  );
  const missingValue = KINDS.some((k) => k.field && kinds.includes(k.id) && !values[k.field]?.trim());
  const why = kinds.length === 0 ? 'Choose what should change' : missingValue ? 'Enter the new value' : undefined;
  return (
    <Modal open={open} title={`Request changes: ${request.symbol} ${request.direction}`} onClose={onClose} wide>
      <div className="grid gap-2 sm:grid-cols-2">
        {KINDS.map((k) => (
          <div key={k.id} className="rounded-md border border-line bg-base-card p-2.5">
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                className="accent-gold"
                checked={kinds.includes(k.id)}
                onChange={(e) => setKinds((a) => (e.target.checked ? [...a, k.id] : a.filter((x) => x !== k.id)))}
              />
              {k.label}
            </label>
            {k.field && kinds.includes(k.id) && (
              <input
                aria-label={`New ${k.label.replace('Change ', '')}`}
                className="field mt-2 w-full"
                inputMode="decimal"
                placeholder={k.field === 'positionSize' ? 'Lots, e.g. 0.05' : 'New price'}
                value={values[k.field] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [k.field as string]: e.target.value }))}
              />
            )}
          </div>
        ))}
      </div>
      <label htmlFor="modify-comment" className="mt-3 block text-xs font-medium text-ink-muted">
        Comment
      </label>
      <textarea id="modify-comment" className="field mt-1 h-16 w-full" maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
      <p className="mt-2 text-xs text-neutral">
        Price or size changes open a new request with a fresh Module 06 assessment. Requests for more analysis go back to the decision engine.
      </p>
      {!!error && <div className="mt-3"><ProblemList title="Changes were not requested" problems={errorProblems(error)} /></div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!!why || pending}
          disabledReason={why}
          loading={pending}
          onClick={() => onSubmit({ clientRequestId, requestVersion: request.version, kinds, changes, comment: comment.trim() || undefined })}
        >
          Request changes
        </Button>
      </div>
    </Modal>
  );
}
