import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Ban, Check, PencilLine, RefreshCw, ShieldAlert, X } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { QueryState } from '@/components/common/QueryState';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { Countdown, DirectionBadge, Field, Modal, ProblemList, RiskBadge, StatusBadge } from '@/components/approvals/ApprovalBits';
import { errorProblems, priceDigits } from '@/utils/approvals';
import { ApproveDialog, ModifyDialog, RejectDialog } from '@/components/approvals/DecisionDialogs';
import { EvidencePanel } from '@/components/approvals/EvidencePanel';
import { useApprovalActions, useApprovalDetail, useApprovalEvidence, useApprovalHistory, useApprovalPolicy } from '@/hooks/useApprovals';
import { canServer, useAuthStore } from '@/stores/authStore';
import { toast } from '@/stores/toastStore';
import { describeError } from '@/utils/errors';
import { formatCurrency, formatDateTime, formatNumber, formatPercent } from '@/utils/format';
import type { ApprovalDetail } from '@/types/approvals';
import { SignInRequired } from './ApprovalsPage';

type Dialog = 'approve' | 'reject' | 'modify' | 'cancel' | null;

export default function ApprovalDetailPage() {
  const { id } = useParams<{ id: string }>();
  const user = useAuthStore((s) => s.user);
  const detail = useApprovalDetail(id);
  if (user?.mode !== 'server') {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader eyebrow="Trade approvals" title="Approval request" />
        <SignInRequired what="Trade approvals" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <Link to="/approvals" className="flex w-fit items-center gap-1 text-xs text-ink-muted hover:text-ink">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All approvals
      </Link>
      <QueryState query={detail}>{(d) => (d.data ? <Detail d={d.data} /> : null)}</QueryState>
    </div>
  );
}

function Detail({ d }: { d: ApprovalDetail }) {
  const r = d.request;
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const evidence = useApprovalEvidence(r.approvalId, r.evidenceSnapshotId);
  const history = useApprovalHistory(r.approvalId);
  const policy = useApprovalPolicy();
  const act = useApprovalActions(r.approvalId);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [cancelReason, setCancelReason] = useState('');
  const digits = priceDigits(r.symbol);
  const open = r.status === 'PENDING' || r.status === 'UNDER_REVIEW';
  const canDecide = canServer(user, 'approvals:decide');
  const seenHash = evidence.data?.data?.hash ?? null;

  // Opening the request marks it "under review". This never approves anything.
  const { mutate: markReview } = act.review;
  useEffect(() => {
    if (r.status === 'PENDING' && canDecide) markReview();
  }, [r.approvalId, r.status, canDecide, markReview]);

  const approveBlocked = !open
    ? `This request is ${r.status.toLowerCase().replace(/_/g, ' ')}`
    : r.riskDecision === 'BLOCK' || r.riskDecision === 'ERROR'
      ? `Module 06 returned ${r.riskDecision}. A blocked trade can never be approved.`
      : !r.canApprove
        ? 'This request can no longer be approved'
        : !canDecide
          ? 'Your role cannot approve trades'
          : undefined;

  return (
    <>
      <PageHeader
        eyebrow={`Trade approval · ${r.strategy.replace(/_/g, ' ')} · ${r.timeframe}`}
        title={`${r.direction === 'BUY' ? 'Buy' : 'Sell'} ${r.symbol}`}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={r.status} />
            <RiskBadge decision={r.riskDecision} />
            {open && (
              <span className="text-xs text-ink-muted">
                Expires in <Countdown expiresAt={r.expiresAt} />
              </span>
            )}
            {r.statusReason && !open && <span className="text-xs text-ink-muted">{r.statusReason}</span>}
          </span>
        }
      />

      {(r.riskDecision === 'BLOCK' || r.riskDecision === 'ERROR') && open && (
        <div role="alert" className="flex gap-2 rounded-card border border-down/40 bg-down-faint px-4 py-3 text-sm text-down-text">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          Module 06 {r.riskDecision === 'BLOCK' ? 'blocked this trade' : 'could not assess this trade'}. It cannot be approved by anyone. You can reject it, or revalidate the risk if conditions
          have changed.
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader title="Trade parameters" subtitle={`Idea ${d.idea.ideaId} · version ${r.ideaVersion} · ${d.idea.source}`} />
            <CardBody>
              <dl className="grid gap-x-8 sm:grid-cols-2">
                <Field label="Direction"><DirectionBadge direction={r.direction} /></Field>
                <Field label="Entry">{formatNumber(r.entry, digits)}</Field>
                <Field label="Stop-loss">{formatNumber(r.stopLoss, digits)}</Field>
                <Field label="Take-profit">{formatNumber(r.takeProfit, digits)}</Field>
                <Field label="Position size (lots)" strong>{r.positionSize == null ? '—' : formatNumber(r.positionSize, 2)}</Field>
                <Field label="Maximum loss at stop" strong>{formatCurrency(r.riskAmount)}</Field>
                <Field label="Risk of equity">{formatPercent(r.riskPct)}</Field>
                <Field label="Reward : risk">{r.riskReward == null ? '—' : `${formatNumber(r.riskReward, 2)} : 1`}</Field>
                <Field label="AI confidence">{r.aiConfidence == null ? '—' : `${formatNumber(r.aiConfidence, 0)} / 100 (not a probability of profit)`}</Field>
                <Field label="Account">{r.accountLogin ?? '—'}</Field>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Risk assessment (Module 06)"
              subtitle={r.riskAssessedAt ? `Assessed ${formatDateTime(r.riskAssessedAt)} UTC · config v${r.riskConfigVersion ?? '—'}` : 'No assessment'}
              actions={
                open && canServer(user, 'approvals:revalidate') ? (
                  <Button
                    size="sm"
                    icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden />}
                    loading={act.revalidate.isPending}
                    onClick={() =>
                      act.revalidate.mutate(undefined, {
                        onSuccess: (x) => toast({ tone: 'info', title: `Risk revalidated: ${x.riskDecision}`, description: 'Review the updated evidence before deciding.' }),
                        onError: (e) => toast({ tone: 'error', title: 'Revalidation failed', description: describeError(e).description }),
                      })
                    }
                  >
                    Revalidate risk
                  </Button>
                ) : undefined
              }
            />
            <CardBody>
              {r.riskWarnings.length === 0 ? (
                <p className="text-sm text-ink-muted">No risk warnings.</p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {r.riskWarnings.map((w) => (
                    <li key={w.ruleId} className="flex gap-2 text-sm">
                      <Badge tone="warn">{w.ruleId}</Badge>
                      <span className="text-ink">{w.message}</span>
                    </li>
                  ))}
                </ul>
              )}
              {d.riskAssessments.length > 1 && (
                <p className="mt-3 text-xs text-neutral">
                  {d.riskAssessments.length} assessments on record:{' '}
                  {d.riskAssessments.map((a) => `${a.purpose} ${a.decision}`).join(' · ')}
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Evidence" subtitle="What supports, contradicts or is missing from this trade idea." />
            <CardBody>
              <QueryState query={evidence}>{(e) => (e.data ? <EvidencePanel evidence={e.data} /> : null)}</QueryState>
            </CardBody>
          </Card>
        </div>

        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader title="Decision" subtitle={r.approvalsRequired > 1 ? `${r.approvalsRequired} approvals required` : undefined} />
            <CardBody className="flex flex-col gap-2">
              <Button variant="primary" icon={<Check className="h-4 w-4" aria-hidden />} disabled={!!approveBlocked} disabledReason={approveBlocked} onClick={() => setDialog('approve')}>
                Approve…
              </Button>
              <Button
                variant="danger"
                icon={<X className="h-4 w-4" aria-hidden />}
                disabled={!open || !canDecide}
                disabledReason={!open ? 'No longer open' : 'Your role cannot reject trades'}
                onClick={() => setDialog('reject')}
              >
                Reject…
              </Button>
              <Button
                icon={<PencilLine className="h-4 w-4" aria-hidden />}
                disabled={!open || !canDecide}
                disabledReason={!open ? 'No longer open' : 'Your role cannot request changes'}
                onClick={() => setDialog('modify')}
              >
                Request changes…
              </Button>
              {canServer(user, 'approvals:cancel') && (
                <Button
                  variant="ghost"
                  icon={<Ban className="h-4 w-4" aria-hidden />}
                  disabled={!(open || r.status === 'APPROVED')}
                  disabledReason="Only open or approved (not yet handed off) requests can be cancelled"
                  onClick={() => setDialog('cancel')}
                >
                  Cancel request…
                </Button>
              )}
              <p className="mt-1 text-2xs text-neutral">{d.disclaimer}</p>
            </CardBody>
          </Card>

          {d.decisions.length > 0 && (
            <Card>
              <CardHeader title="Decisions" />
              <CardBody>
                <ul className="flex flex-col gap-2 text-sm">
                  {d.decisions.map((x) => (
                    <li key={x.recordHash} className="border-b border-line-soft pb-2 last:border-0">
                      <span className="font-medium text-ink">{x.decision.replace(/_/g, ' ').toLowerCase()}</span> by {x.username}{' '}
                      <span className="text-xs text-neutral">({x.role})</span>
                      <div className="num text-xs text-neutral">{formatDateTime(x.decidedAt)} UTC</div>
                      {x.reasonCode && <div className="text-xs text-ink-muted">Reason: {x.reasonCode.replace(/_/g, ' ')}</div>}
                      {x.comment && <div className="text-xs text-ink-muted">“{x.comment}”</div>}
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          )}

          {d.handoff && (
            <Card>
              <CardHeader title="Execution handoff" subtitle="Module 09 validates again before any order" />
              <CardBody>
                <dl>
                  <Field label="Request">{d.handoff.executionRequestId.slice(0, 8)}</Field>
                  <Field label="Status">{d.handoff.status}</Field>
                  <Field label="Picked up">{d.handoff.pickedUpAt ? formatDateTime(d.handoff.pickedUpAt) : 'Not yet (Module 09 is not built)'}</Field>
                </dl>
                {d.handoff.results.map((x) => (
                  <p key={x.eventId} className="mt-2 text-xs text-ink-muted">
                    {formatDateTime(x.reportedAt)} · {x.status}
                    {x.brokerTicket && ` · ticket ${x.brokerTicket}`}
                    {x.message && ` · ${x.message}`}
                  </p>
                ))}
              </CardBody>
            </Card>
          )}

          {d.relatedRequests.length > 1 && (
            <Card>
              <CardHeader title="Versions of this idea" />
              <CardBody>
                <ul className="flex flex-col gap-1 text-sm">
                  {d.relatedRequests.map((x) => (
                    <li key={x.approvalId} className="flex items-center justify-between gap-2">
                      {x.approvalId === r.approvalId ? (
                        <span className="text-ink">v{x.ideaVersion} (this request)</span>
                      ) : (
                        <Link to={`/approvals/${x.approvalId}`} className="text-ink hover:underline">
                          v{x.ideaVersion}
                        </Link>
                      )}
                      <StatusBadge status={x.status} />
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader title="History" subtitle="Tamper-evident audit trail" />
            <CardBody>
              <QueryState query={history} compact>
                {(h) => (
                  <ol className="flex flex-col gap-2 text-xs">
                    {(h.data?.items ?? []).map((a) => (
                      <li key={a.seq}>
                        <span className="num text-neutral">{formatDateTime(a.at)}</span> · <span className="text-ink">{a.action.replace(/[._]/g, ' ')}</span> by {a.actor}
                        {a.reason && <div className="text-ink-muted">{a.reason}</div>}
                      </li>
                    ))}
                  </ol>
                )}
              </QueryState>
            </CardBody>
          </Card>
        </div>
      </div>

      <ApproveDialog
        open={dialog === 'approve'}
        request={r}
        evidenceHash={seenHash}
        pending={act.approve.isPending}
        error={act.approve.error}
        onClose={() => (setDialog(null), act.approve.reset())}
        onSubmit={(b) =>
          act.approve.mutate(b, {
            onSuccess: (x) => {
              setDialog(null);
              toast({
                tone: x.status === 'INVALIDATED' ? 'warning' : 'success',
                title: x.status === 'INVALIDATED' ? 'Approved, but final validation failed' : 'Trade approved',
                description: x.status === 'INVALIDATED' ? x.statusReason ?? undefined : 'Handed to Module 09 for execution checks.',
              });
            },
          })
        }
      />
      <RejectDialog
        open={dialog === 'reject'}
        request={r}
        reasons={policy.data?.data?.policy.review.rejectionReasons ?? ['trader_discretion', 'other']}
        pending={act.reject.isPending}
        error={act.reject.error}
        onClose={() => (setDialog(null), act.reject.reset())}
        onSubmit={(b) => act.reject.mutate(b, { onSuccess: () => (setDialog(null), toast({ tone: 'info', title: 'Trade rejected' })) })}
      />
      <ModifyDialog
        open={dialog === 'modify'}
        request={r}
        pending={act.modify.isPending}
        error={act.modify.error}
        onClose={() => (setDialog(null), act.modify.reset())}
        onSubmit={(b) =>
          act.modify.mutate(b, {
            onSuccess: (x) => {
              setDialog(null);
              toast({ tone: 'info', title: 'Changes requested', description: x.newApproval ? 'A new request with a fresh risk assessment was opened.' : 'Sent back to the decision engine.' });
              if (x.newApproval) navigate(`/approvals/${x.newApproval.approvalId}`);
            },
          })
        }
      />
      <Modal open={dialog === 'cancel'} title="Cancel this request?" onClose={() => (setDialog(null), act.cancel.reset())}>
        <label htmlFor="cancel-reason" className="text-xs font-medium text-ink-muted">
          Reason (required)
        </label>
        <textarea id="cancel-reason" className="field mt-1 h-16 w-full" value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} />
        {!!act.cancel.error && <div className="mt-3"><ProblemList title="Not cancelled" problems={errorProblems(act.cancel.error)} /></div>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDialog(null)}>
            Keep it
          </Button>
          <Button
            variant="danger"
            disabled={!cancelReason.trim()}
            disabledReason="Give a reason"
            loading={act.cancel.isPending}
            onClick={() => act.cancel.mutate(cancelReason.trim(), { onSuccess: () => (setDialog(null), toast({ tone: 'info', title: 'Request cancelled' })) })}
          >
            Cancel request
          </Button>
        </div>
      </Modal>
    </>
  );
}
