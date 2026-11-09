import { useMemo, useState } from 'react';
import { AlertOctagon, PencilLine } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { DataTable, type Column } from '@/components/common/DataTable';
import { EmptyState } from '@/components/common/EmptyState';
import { QueryState } from '@/components/common/QueryState';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { useApprovalPolicy, useManagementActions, useManagementRequests, usePositions } from '@/hooks/useApprovals';
import { newRequestId } from '@/services/approvalService';
import { canServer, useAuthStore } from '@/stores/authStore';
import { toast } from '@/stores/toastStore';
import { formatCurrency, formatDateTime, formatNumber, relativeTime } from '@/utils/format';
import type { ManagedPosition, ManagementAction, ManagementRequest } from '@/types/approvals';
import { Countdown, DirectionBadge, Field, Modal, ProblemList } from './ApprovalBits';
import { errorProblems, priceDigits } from '@/utils/approvals';

const ACTIONS: { id: ManagementAction; label: string; params: { key: string; label: string; hint?: string }[] }[] = [
  { id: 'MOVE_STOP_LOSS', label: 'Move stop-loss', params: [{ key: 'stopLoss', label: 'New stop-loss', hint: 'Only towards the price: widening a stop is refused.' }] },
  { id: 'BREAKEVEN', label: 'Stop to breakeven', params: [] },
  { id: 'TRAILING_STOP', label: 'Trailing stop', params: [{ key: 'distance', label: 'Distance (price units)', hint: 'e.g. 0.00100 for 10 pips on EURUSD' }] },
  { id: 'MOVE_TAKE_PROFIT', label: 'Move take-profit', params: [{ key: 'takeProfit', label: 'New take-profit' }] },
  { id: 'PARTIAL_CLOSE', label: 'Partial close', params: [{ key: 'fraction', label: 'Fraction to close', hint: 'e.g. 0.5 for half' }] },
  { id: 'CLOSE', label: 'Close position', params: [] },
  { id: 'EMERGENCY_EXIT', label: 'Emergency exit', params: [] },
];

const actionLabel = (a: string) => ACTIONS.find((x) => x.id === a)?.label ?? a.replace(/_/g, ' ').toLowerCase();

/**
 * Position changes as requests for a person to approve (Module 07). An approved request becomes an instruction for
 * Module 09, which re-checks the position before acting. Module 09 is not built yet, so approved changes wait in its
 * queue; nothing here touches the broker.
 */
export function PositionManagement() {
  const user = useAuthStore((s) => s.user);
  const positions = usePositions();
  const requests = useManagementRequests();
  const [target, setTarget] = useState<ManagedPosition | null>(null);
  const [deciding, setDeciding] = useState<ManagementRequest | null>(null);
  const canRequest = canServer(user, 'management:request');
  const pv = positions.data?.data;

  const cols: Column<ManagedPosition>[] = [
    { key: 'id', header: 'Position', cell: (r) => <span className="num text-neutral">{r.positionId}</span> },
    { key: 'sym', header: 'Instrument', cell: (r) => <span className="num font-semibold text-ink">{r.symbol}</span> },
    { key: 'side', header: 'Side', cell: (r) => <DirectionBadge direction={r.side} /> },
    { key: 'vol', header: 'Volume', align: 'right', cell: (r) => <span className="num">{formatNumber(r.volume, 2)}</span> },
    { key: 'entry', header: 'Entry', align: 'right', cell: (r) => <span className="num">{formatNumber(r.entryPrice, priceDigits(r.symbol))}</span> },
    { key: 'cur', header: 'Current', align: 'right', cell: (r) => <span className="num">{r.currentPrice == null ? 'n/a' : formatNumber(r.currentPrice, priceDigits(r.symbol))}</span> },
    { key: 'sl', header: 'Stop', align: 'right', cell: (r) => <span className="num">{r.stopLoss == null ? 'none' : formatNumber(r.stopLoss, priceDigits(r.symbol))}</span> },
    { key: 'tp', header: 'Target', align: 'right', cell: (r) => <span className="num">{r.takeProfit == null ? 'none' : formatNumber(r.takeProfit, priceDigits(r.symbol))}</span> },
    {
      key: 'pnl',
      header: 'P/L',
      align: 'right',
      cell: (r) => <span className={(r.unrealizedPnl ?? 0) >= 0 ? 'num text-up' : 'num text-down-text'}>{formatCurrency(r.unrealizedPnl)}</span>,
    },
    {
      key: 'act',
      header: '',
      align: 'right',
      cell: (r) => (
        <Button size="sm" icon={<PencilLine className="h-3.5 w-3.5" aria-hidden />} disabled={!canRequest} disabledReason="Your role cannot request position changes" onClick={() => setTarget(r)}>
          Request change
        </Button>
      ),
    },
  ];

  if (user?.mode !== 'server') {
    return (
      <Card>
        <CardHeader title="Position management (human approval)" />
        <CardBody>
          <p className="text-sm text-ink-muted">Sign in with a backend account to request or approve changes to open positions.</p>
        </CardBody>
      </Card>
    );
  }

  const pending = (requests.data?.data?.items ?? []).filter((x) => x.status === 'PENDING');
  const recent = (requests.data?.data?.items ?? []).filter((x) => x.status !== 'PENDING').slice(0, 10);

  return (
    <>
      <Card>
        <CardHeader
          title="Position management (human approval)"
          subtitle={
            pv
              ? `Source: ${pv.source === 'module09' ? 'Module 09' : 'account mirror'}${pv.asOf ? ` · updated ${relativeTime(pv.asOf)}` : ''}`
              : undefined
          }
          actions={pv?.stale ? <Badge tone="warn">Data may be stale</Badge> : undefined}
        />
        {pv?.note && <CardBody className="pt-0 text-xs text-ink-muted">{pv.note}</CardBody>}
        {pv?.error && <CardBody className="pt-0 text-xs text-down-text">{pv.error}</CardBody>}
        <QueryState query={positions}>
          {() => (
            <DataTable
              caption="Open positions that can be managed"
              columns={cols}
              rows={pv?.positions ?? []}
              rowKey={(r) => r.positionId}
              empty={<EmptyState title="No open positions" description="Positions appear here from the account mirror (sync-account) until Module 09 reports them." />}
            />
          )}
        </QueryState>
      </Card>

      <Card>
        <CardHeader title="Change requests" subtitle="Every change to a position needs a person's approval. Emergency exits need the typed confirmation phrase." />
        <CardBody>
          {pending.length === 0 && recent.length === 0 ? (
            <p className="text-sm text-ink-muted">No change requests.</p>
          ) : (
            <ul className="divide-y divide-line-soft">
              {[...pending, ...recent].map((x) => (
                <li key={x.requestId} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    {x.priority === 'emergency' && <Badge tone="down">Emergency</Badge>}
                    <span className="font-medium text-ink">{actionLabel(x.action)}</span>
                    <span className="num text-ink-muted">
                      {x.symbol} #{x.positionId}
                    </span>
                    {Object.entries(x.params)
                      .filter(([k]) => k !== 'note')
                      .map(([k, v]) => (
                        <span key={k} className="num text-xs text-neutral">
                          {k.replace(/([A-Z])/g, ' $1').toLowerCase()} {v}
                        </span>
                      ))}
                  </span>
                  <span className="flex items-center gap-2">
                    {x.status === 'PENDING' ? (
                      <>
                        <span className="text-xs text-ink-muted">
                          expires in <Countdown expiresAt={x.expiresAt} />
                        </span>
                        <Button size="sm" variant="primary" disabled={!canServer(user, 'management:decide')} disabledReason="Your role cannot decide position changes" onClick={() => setDeciding(x)}>
                          Review…
                        </Button>
                      </>
                    ) : (
                      <Badge tone={x.status.startsWith('EXECUT') || x.status === 'APPROVED' ? 'info' : 'neutral'}>{x.status.replace(/_/g, ' ').toLowerCase()}</Badge>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      {target && <RequestDialog position={target} onClose={() => setTarget(null)} />}
      {deciding && <DecideDialog request={deciding} onClose={() => setDeciding(null)} />}
    </>
  );
}

function RequestDialog({ position, onClose }: { position: ManagedPosition; onClose: () => void }) {
  const user = useAuthStore((s) => s.user);
  const { create } = useManagementActions();
  const [action, setAction] = useState<ManagementAction>('MOVE_STOP_LOSS');
  const [params, setParams] = useState<Record<string, string>>({});
  const [reason, setReason] = useState('');
  const clientRequestId = useMemo(() => newRequestId(), []);
  const spec = ACTIONS.find((a) => a.id === action)!;
  const allowed = ACTIONS.filter((a) => a.id !== 'EMERGENCY_EXIT' || canServer(user, 'management:emergency'));
  const missing = spec.params.some((p) => !params[p.key]?.trim());
  const why = missing ? 'Enter the value' : reason.trim().length < 3 ? 'Give a reason' : undefined;
  return (
    <Modal open title={`Request a change: ${position.symbol} #${position.positionId}`} onClose={onClose}>
      <dl className="rounded-md border border-line bg-base-card px-3 py-1">
        <Field label="Side / volume">{`${position.side} ${formatNumber(position.volume, 2)}`}</Field>
        <Field label="Entry / current">{`${formatNumber(position.entryPrice, priceDigits(position.symbol))} / ${position.currentPrice == null ? 'n/a' : formatNumber(position.currentPrice, priceDigits(position.symbol))}`}</Field>
        <Field label="Stop / target">{`${position.stopLoss ?? 'none'} / ${position.takeProfit ?? 'none'}`}</Field>
      </dl>
      <label htmlFor="m-action" className="mt-3 block text-xs font-medium text-ink-muted">
        Action
      </label>
      <select id="m-action" className="field mt-1 w-full" value={action} onChange={(e) => (setAction(e.target.value as ManagementAction), setParams({}))}>
        {allowed.map((a) => (
          <option key={a.id} value={a.id}>
            {a.label}
          </option>
        ))}
      </select>
      {spec.params.map((p) => (
        <div key={p.key} className="mt-3">
          <label htmlFor={`m-${p.key}`} className="block text-xs font-medium text-ink-muted">
            {p.label}
          </label>
          <input id={`m-${p.key}`} className="field mt-1 w-full" inputMode="decimal" value={params[p.key] ?? ''} onChange={(e) => setParams((v) => ({ ...v, [p.key]: e.target.value }))} />
          {p.hint && <p className="mt-1 text-2xs text-neutral">{p.hint}</p>}
        </div>
      ))}
      {action === 'EMERGENCY_EXIT' && (
        <p className="mt-3 flex gap-2 rounded-md bg-down-faint p-2.5 text-xs text-down-text">
          <AlertOctagon className="h-3.5 w-3.5 shrink-0" aria-hidden /> Closes the whole position at market once authorised with the typed confirmation phrase.
        </p>
      )}
      <label htmlFor="m-reason" className="mt-3 block text-xs font-medium text-ink-muted">
        Reason
      </label>
      <textarea id="m-reason" className="field mt-1 h-16 w-full" value={reason} onChange={(e) => setReason(e.target.value)} />
      {!!create.error && <div className="mt-3"><ProblemList title="Request not created" problems={errorProblems(create.error)} /></div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant={action === 'EMERGENCY_EXIT' ? 'danger' : 'primary'}
          disabled={!!why}
          disabledReason={why}
          loading={create.isPending}
          onClick={() =>
            create.mutate(
              { positionId: position.positionId, action, params, reason: reason.trim(), clientRequestId },
              { onSuccess: () => (onClose(), toast({ tone: 'info', title: 'Change requested', description: 'It now needs a person to approve it.' })) },
            )
          }
        >
          Submit for approval
        </Button>
      </div>
    </Modal>
  );
}

function DecideDialog({ request, onClose }: { request: ManagementRequest; onClose: () => void }) {
  const { decide } = useManagementActions();
  const policy = useApprovalPolicy();
  const [confirmed, setConfirmed] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [comment, setComment] = useState('');
  const clientRequestId = useMemo(() => newRequestId(), []);
  const emergency = request.priority === 'emergency';
  const expected = (policy.data?.data?.policy.management.emergency.confirmationPhrase ?? 'CLOSE {symbol}').replace('{symbol}', request.symbol);
  const approveWhy = emergency ? (phrase.trim() !== expected ? `Type: ${expected}` : undefined) : !confirmed ? 'Tick the confirmation' : undefined;
  const send = (decision: 'approve' | 'reject') =>
    decide.mutate(
      {
        id: request.requestId,
        decision,
        body: {
          clientRequestId,
          requestVersion: request.version,
          comment: comment.trim() || undefined,
          confirmation: emergency ? { phrase: phrase.trim() } : { action: request.action },
        },
      },
      { onSuccess: () => (onClose(), toast({ tone: 'info', title: decision === 'approve' ? 'Change approved and sent to Module 09' : 'Change rejected' })) },
    );
  return (
    <Modal open title={`${emergency ? 'Emergency exit' : actionLabel(request.action)}: ${request.symbol} #${request.positionId}`} onClose={onClose}>
      <dl className="rounded-md border border-line bg-base-card px-3 py-1">
        {Object.entries(request.params).map(([k, v]) => (
          <Field key={k} label={k.replace(/([A-Z])/g, ' $1')}>{v}</Field>
        ))}
        {Object.entries(request.riskImplications ?? {}).map(([k, v]) => (
          <Field key={k} label={k.replace(/([A-Z])/g, ' $1')}>{v ?? '—'}</Field>
        ))}
        <Field label="Requested">{formatDateTime(request.createdAt)} UTC</Field>
      </dl>
      <p className="mt-2 text-sm text-ink-muted">Reason: {request.reason}</p>
      {emergency ? (
        <div className="mt-3">
          <label htmlFor="d-phrase" className="block text-xs font-medium text-ink-muted">
            Type <span className="num font-semibold text-ink">{expected}</span> to authorise
          </label>
          <input id="d-phrase" className="field mt-1 w-full" autoComplete="off" value={phrase} onChange={(e) => setPhrase(e.target.value)} />
        </div>
      ) : (
        <label className="mt-3 flex items-start gap-2 text-sm text-ink">
          <input type="checkbox" className="mt-1 accent-gold" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          <span>I approve: {actionLabel(request.action).toLowerCase()} on this position, as shown.</span>
        </label>
      )}
      <label htmlFor="d-comment" className="mt-3 block text-xs font-medium text-ink-muted">
        Comment (required to reject)
      </label>
      <textarea id="d-comment" className="field mt-1 h-14 w-full" value={comment} onChange={(e) => setComment(e.target.value)} />
      {!!decide.error && <div className="mt-3"><ProblemList title="Not decided" problems={errorProblems(decide.error)} /></div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
        <Button variant="danger" disabled={!comment.trim()} disabledReason="Add a comment to reject" loading={decide.isPending} onClick={() => send('reject')}>
          Reject
        </Button>
        <Button variant="primary" disabled={!!approveWhy} disabledReason={approveWhy} loading={decide.isPending} onClick={() => send('approve')}>
          Approve
        </Button>
      </div>
    </Modal>
  );
}
