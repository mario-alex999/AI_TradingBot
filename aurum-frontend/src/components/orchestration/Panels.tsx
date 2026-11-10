import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Play, RefreshCw } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { MetricCard } from '@/components/common/MetricCard';
import { DataTable, type Column } from '@/components/common/DataTable';
import { EmptyState } from '@/components/common/EmptyState';
import { QueryState } from '@/components/common/QueryState';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { Drawer } from '@/components/common/Drawer';
import { Field } from '@/components/approvals/ApprovalBits';
import { canServer, useAuthStore } from '@/stores/authStore';
import { toast } from '@/stores/toastStore';
import { describeError } from '@/utils/errors';
import { formatDateTime, formatNumber, formatPercent } from '@/utils/format';
import {
  useCancelExecution,
  useDeadLetters,
  useDeliveries,
  useExecution,
  useExecutions,
  useIntegrations,
  useOrchAudit,
  useOrchConfig,
  useOrchEvents,
  useOrchOverview,
  useReplayEvent,
  useResolveDeadLetter,
  useRetryDeadLetter,
  useRetryExecution,
  useSetWorkflowState,
  useTestIntegration,
  useTriggerTest,
  useUpdateConfig,
  useWorkflows,
} from '@/hooks/useOrchestration';
import type { AuditRow, DeadLetter, Execution, ExecutionStatus, Integration, OrchEvent, Workflow } from '@/types/orchestration';
import { TEST_WORKFLOW_ID, shortId } from '@/utils/orchestration';
import { ExecStatusBadge, HealthBadge, ReasonDialog, WorkflowStateBadge } from './OrchBits';
const when = (iso: string | null | undefined) => <span className="num text-xs text-ink-muted">{iso ? formatDateTime(iso) : '—'}</span>;
const useCan = (perm: string) => canServer(useAuthStore((s) => s.user), perm);

// ---- Overview ---------------------------------------------------------------------------------------------------------------
export function OverviewPanel({ onOpenExecution }: { onOpenExecution: (id: string) => void }) {
  const q = useOrchOverview();
  const o = q.data?.data;
  const trigger = useTriggerTest();
  const canOperate = useCan('orchestration:operate');
  const testWf = useWorkflows().data?.data?.items.find((w) => w.workflowId === TEST_WORKFLOW_ID);
  const testActive = testWf?.state === 'ACTIVE' || testWf?.state === 'DEGRADED';
  return (
    <QueryState query={q} isEmpty={(d) => !d.data} empty={<EmptyState title="No orchestration data" description="The Module 08 backend did not return an overview." />}>
      {() =>
        o && (
          <div className="flex flex-col gap-5">
            {o.alerts.map((a) => (
              <div key={a.message} role="alert" className={a.level === 'critical' ? 'rounded-md border border-down/40 bg-down-faint p-3 text-sm text-down-text' : 'rounded-md border border-warn/40 bg-base-raised p-3 text-sm text-warn'}>
                <AlertTriangle className="mr-2 inline h-4 w-4" aria-hidden />
                {a.message}
              </div>
            ))}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <MetricCard label="Active workflows" value={<span className="num">{o.workflows.active} / {o.workflows.total}</span>} />
              <MetricCard label="In flight" value={<span className="num">{o.executions.running}</span>} info="Sent to n8n and not finished yet." />
              <MetricCard label="Queued" value={<span className="num">{o.executions.queued + o.executions.retryPending}</span>} info="Waiting to be sent, including retries with back-off." />
              <MetricCard label="Success rate (24 h)" value={<span className="num">{o.last24h.successRate == null ? '—' : formatPercent(o.last24h.successRate * 100, 0)}</span>} />
              <MetricCard label="Dead letters" value={<span className="num">{o.deadLettersOpen}</span>} tone={o.deadLettersOpen ? 'down' : 'flat'} info="Work that failed for good and needs a person's decision." />
              <MetricCard label="Events (24 h)" value={<span className="num">{o.last24h.events}</span>} />
            </div>
            <Card>
              <CardHeader
                title="Harmless test workflow"
                subtitle="Sends a message to the n8n test-echo workflow and waits for its callback. It touches no market, broker or account. Use it to prove the n8n connection end to end."
                actions={
                  canOperate && (
                    <Button
                      size="sm"
                      icon={<Play className="h-3.5 w-3.5" aria-hidden />}
                      loading={trigger.isPending}
                      disabled={!testActive}
                      disabledReason="Activate the test workflow first (python -m aurum.cli orchestration setup-test-workflow)"
                      onClick={() =>
                        trigger.mutate(
                          { workflowId: TEST_WORKFLOW_ID, input: { message: 'hello from the AURUM dashboard' } },
                          {
                            onSuccess: (r) => {
                              toast({ tone: 'success', title: 'Test workflow triggered', description: `Execution ${shortId(r.executionId)}: ${r.status}` });
                              onOpenExecution(r.executionId);
                            },
                            onError: (e) => toast({ tone: 'error', title: 'Could not trigger', description: describeError(e).description }),
                          },
                        )
                      }
                    >
                      Run test
                    </Button>
                  )
                }
              />
              <CardBody className="text-xs text-ink-muted">
                Environment <Badge tone="info">{o.environment}</Badge> · n8n{' '}
                {o.integrations.n8n ? <HealthBadge status={o.integrations.n8n.status} /> : '—'} {o.integrations.n8n?.detail ?? ''}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Recent executions" />
              <ExecutionTable rows={o.recentExecutions} onOpen={onOpenExecution} />
            </Card>
          </div>
        )
      }
    </QueryState>
  );
}

// ---- Workflow registry ------------------------------------------------------------------------------------------------------
export function WorkflowsPanel() {
  const q = useWorkflows();
  const setState = useSetWorkflowState();
  const canManage = useCan('orchestration:manage');
  const [target, setTarget] = useState<{ wf: Workflow; state: string } | null>(null);
  const cols: Column<Workflow>[] = [
    { key: 'id', header: 'Workflow', sortValue: (w) => w.workflowId, cell: (w) => (
      <span className="flex flex-col"><span className="font-medium text-ink">{w.name}</span><span className="num text-xs text-ink-muted">{w.workflowId}</span></span>
    ) },
    { key: 'cat', header: 'Category', cell: (w) => <span className="text-ink-muted">{w.category.replace(/_/g, ' ')}</span> },
    { key: 'state', header: 'State', sortValue: (w) => w.state, cell: (w) => <WorkflowStateBadge state={w.state} placeholder={w.placeholder} /> },
    { key: 'ver', header: 'Version', cell: (w) => <span className="num">{w.activeVersion ?? '—'}</span> },
    { key: 'n8n', header: 'n8n id', cell: (w) => <span className="num text-xs text-ink-muted">{w.n8nWorkflowId ?? '—'}</span> },
    { key: 'trig', header: 'Triggers', cell: (w) => <span className="text-xs text-ink-muted">{w.triggerTypes.join(', ')}</span> },
    { key: 'last', header: 'Last run', sortValue: (w) => w.lastExecutionAt, cell: (w) => when(w.lastExecutionAt) },
    ...(canManage
      ? [{ key: 'act', header: '', align: 'right' as const, cell: (w: Workflow) =>
          w.placeholder ? null : w.state === 'ACTIVE' || w.state === 'DEGRADED' ? (
            <Button size="sm" variant="ghost" onClick={() => setTarget({ wf: w, state: 'PAUSED' })}>Pause</Button>
          ) : w.state === 'PAUSED' ? (
            <Button size="sm" variant="ghost" onClick={() => setTarget({ wf: w, state: 'ACTIVE' })}>Resume</Button>
          ) : null }]
      : []),
  ];
  return (
    <Card>
      <CardHeader title="Workflow registry" subtitle="Workflows are built in n8n, then registered and versioned here. Reserved entries (WF08-01 to 04) are placeholders until they are built." />
      <QueryState query={q}>
        {(d) => <DataTable caption="Registered workflows" columns={cols} rows={d.data?.items ?? []} rowKey={(w) => w.workflowId} empty={<EmptyState title="No workflows registered" />} />}
      </QueryState>
      <ReasonDialog
        open={!!target}
        title={target?.state === 'PAUSED' ? `Pause ${target?.wf.name}` : `Resume ${target?.wf.name ?? ''}`}
        description={target?.state === 'PAUSED' ? 'Queued executions wait while it is paused; nothing new is sent to n8n.' : 'Queued executions are sent again.'}
        confirmLabel={target?.state === 'PAUSED' ? 'Pause workflow' : 'Resume workflow'}
        pending={setState.isPending}
        error={setState.error}
        onClose={() => (setTarget(null), setState.reset())}
        onSubmit={(reason) =>
          target && setState.mutate({ id: target.wf.workflowId, state: target.state, reason }, { onSuccess: () => (setTarget(null), toast({ tone: 'success', title: 'Workflow updated' })) })
        }
      />
    </Card>
  );
}

// ---- Executions -------------------------------------------------------------------------------------------------------------
function ExecutionTable({ rows, onOpen }: { rows: Execution[]; onOpen: (id: string) => void }) {
  const cols: Column<Execution>[] = [
    { key: 'id', header: 'Execution', cell: (e) => <span className="num text-ink">{shortId(e.executionId)}</span> },
    { key: 'wf', header: 'Workflow', sortValue: (e) => e.workflowId, cell: (e) => <span className="text-ink-muted">{e.workflowId}</span> },
    { key: 'st', header: 'Status', sortValue: (e) => e.status, cell: (e) => <ExecStatusBadge status={e.status} /> },
    { key: 'tr', header: 'Trigger', cell: (e) => <span className="text-xs text-ink-muted">{e.triggerType}</span> },
    { key: 'att', header: 'Attempt', align: 'right', cell: (e) => <span className="num">{e.attempt}</span> },
    { key: 'err', header: 'Error', cell: (e) => <span className="text-xs text-down-text">{e.errorCategory ?? ''}</span> },
    { key: 'dur', header: 'Duration', align: 'right', sortValue: (e) => e.durationSeconds, cell: (e) => <span className="num">{e.durationSeconds == null ? '—' : `${formatNumber(e.durationSeconds, 1)} s`}</span> },
    { key: 'cr', header: 'Created (UTC)', sortValue: (e) => e.createdAt, cell: (e) => when(e.createdAt) },
  ];
  return (
    <DataTable caption="Workflow executions" columns={cols} rows={rows} rowKey={(e) => e.executionId} onRowClick={(e) => onOpen(e.executionId)}
      rowLabel={(e) => `Open execution ${shortId(e.executionId)}`} empty={<EmptyState title="No executions yet" description="Executions appear here when a workflow is triggered." />} />
  );
}

const STATUSES: ExecutionStatus[] = ['QUEUED', 'DISPATCHED', 'WAITING_CALLBACK', 'RUNNING', 'RETRY_PENDING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'DEAD_LETTERED', 'CANCELLED'];

export function ExecutionsPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const [status, setStatus] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [page, setPage] = useState(1);
  const q = useExecutions({ status, workflowId, page, pageSize: 50 });
  const total = q.data?.data?.total ?? 0;
  return (
    <Card>
      <CardBody className="flex flex-wrap items-end gap-3 border-b border-line-soft">
        <label className="flex flex-col gap-1 text-xs text-ink-muted" htmlFor="x-status">
          Status
          <select id="x-status" className="field" value={status} onChange={(e) => (setStatus(e.target.value), setPage(1))}>
            <option value="">Any</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-muted" htmlFor="x-wf">
          Workflow id
          <input id="x-wf" className="field w-56" value={workflowId} onChange={(e) => (setWorkflowId(e.target.value.trim()), setPage(1))} placeholder="e.g. aurum-core-test-echo" />
        </label>
      </CardBody>
      <QueryState query={q}>{(d) => <ExecutionTable rows={d.data?.items ?? []} onOpen={onOpen} />}</QueryState>
      {total > 50 && (
        <CardBody className="flex items-center justify-between border-t border-line-soft text-xs text-ink-muted">
          <span>Page {page} of {Math.ceil(total / 50)}</span>
          <span className="flex gap-2">
            <Button size="sm" variant="ghost" disabled={page <= 1} disabledReason="First page" onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <Button size="sm" variant="ghost" disabled={page * 50 >= total} disabledReason="Last page" onClick={() => setPage((p) => p + 1)}>Next</Button>
          </span>
        </CardBody>
      )}
    </Card>
  );
}

export function ExecutionDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useExecution(id);
  const e = q.data?.data;
  const retry = useRetryExecution();
  const cancel = useCancelExecution();
  const canOperate = useCan('orchestration:operate');
  const [dialog, setDialog] = useState<'retry' | 'cancel' | null>(null);
  const retryable = e && ['FAILED', 'TIMED_OUT', 'DEAD_LETTERED'].includes(e.status);
  const cancellable = e && ['RECEIVED', 'VALIDATING', 'QUEUED', 'DISPATCHED', 'RUNNING', 'WAITING_CALLBACK', 'RETRY_PENDING'].includes(e.status);
  const unknownOutcome = e && (e.status === 'TIMED_OUT' || e.errorCategory === 'OUTCOME_UNKNOWN' || e.errorCategory === 'TIMED_OUT');
  return (
    <Drawer open={!!id} title={`Execution ${shortId(id)}`} subtitle={e?.workflowId} onClose={onClose} width="w-[min(96vw,620px)]">
      <QueryState query={q}>
        {() =>
          e && (
            <div className="flex flex-col gap-4 text-sm">
              <div className="flex items-center gap-2"><ExecStatusBadge status={e.status} /><span className="text-xs text-ink-muted">attempt {e.attempt}, version {e.workflowVersion}</span></div>
              {e.errorMessage && <p role="alert" className="rounded-md border border-down/40 bg-down-faint p-2 text-xs text-down-text">{e.errorCategory}: {e.errorMessage}</p>}
              {e.warnings.map((w) => <p key={w} className="text-xs text-warn">{w}</p>)}
              <dl>
                <Field label="Execution id">{e.executionId}</Field>
                <Field label="n8n execution">{e.n8nExecutionId ?? '—'}</Field>
                <Field label="Correlation id">{e.correlationId}</Field>
                <Field label="Trigger">{e.triggerType} by {e.triggerSource}</Field>
                <Field label="Idempotency key">{e.idempotencyKey}</Field>
                <Field label="Deadline (UTC)">{e.deadlineAt ? formatDateTime(e.deadlineAt) : '—'}</Field>
              </dl>
              {canOperate && (
                <div className="flex gap-2">
                  <Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden />} disabled={!retryable} disabledReason="Only failed, timed-out or dead-lettered executions can be retried" onClick={() => setDialog('retry')}>Retry</Button>
                  <Button size="sm" variant="danger" disabled={!cancellable} disabledReason="This execution has already finished" onClick={() => setDialog('cancel')}>Cancel</Button>
                </div>
              )}
              <section>
                <h3 className="eyebrow mb-1">Timeline</h3>
                <ol className="space-y-1 text-xs">
                  {e.timeline.map((t, i) => (
                    <li key={i} className="flex gap-2"><span className="num text-ink-muted">{formatDateTime(t.at)}</span><span className="text-ink">{t.from ?? '·'} → {t.to}</span><span className="text-ink-muted">{t.by}{t.reason ? ` — ${t.reason}` : ''}</span></li>
                  ))}
                </ol>
              </section>
              <section>
                <h3 className="eyebrow mb-1">Dispatch attempts</h3>
                <ul className="space-y-1 text-xs">{e.attempts.map((a) => <li key={a.attempt}>#{a.attempt} {a.outcome}{a.httpStatus ? ` (HTTP ${a.httpStatus})` : ''}{a.errorCategory ? ` — ${a.errorCategory}` : ''}</li>)}</ul>
              </section>
              <section>
                <h3 className="eyebrow mb-1">Callbacks from n8n</h3>
                <ul className="space-y-1 text-xs">{e.callbacks.length ? e.callbacks.map((c) => <li key={c.callbackId}>{c.kind} → {c.outcome}{c.detail ? ` (${c.detail})` : ''}</li>) : <li className="text-ink-muted">None yet.</li>}</ul>
              </section>
              <section>
                <h3 className="eyebrow mb-1">Input / output</h3>
                <pre className="max-h-48 overflow-auto rounded-md bg-base-raised p-2 text-xs">{JSON.stringify({ input: e.input, output: e.output }, null, 2)}</pre>
              </section>
            </div>
          )
        }
      </QueryState>
      <ReasonDialog
        open={dialog === 'retry'}
        title="Retry execution"
        description="Sends the execution to n8n again with a new attempt number. Business failures (for example a risk block) cannot be retried."
        confirmLabel="Retry"
        extra={unknownOutcome ? { label: 'I checked n8n: the previous attempt did not run', hint: 'The last attempt timed out, so it may have run. Retrying could run it twice.' } : undefined}
        pending={retry.isPending}
        error={retry.error}
        onClose={() => (setDialog(null), retry.reset())}
        onSubmit={(reason, confirm) => id && retry.mutate({ id, reason, confirmUnknownOutcome: confirm }, { onSuccess: () => (setDialog(null), toast({ tone: 'success', title: 'Retry sent' })) })}
      />
      <ReasonDialog
        open={dialog === 'cancel'}
        title="Cancel execution"
        description="Stops waiting for this execution and asks n8n to stop it when its API is configured. If n8n already finished the work, that is not undone."
        confirmLabel="Cancel execution"
        danger
        pending={cancel.isPending}
        error={cancel.error}
        onClose={() => (setDialog(null), cancel.reset())}
        onSubmit={(reason) => id && cancel.mutate({ id, reason }, { onSuccess: () => (setDialog(null), toast({ tone: 'success', title: 'Execution cancelled' })) })}
      />
    </Drawer>
  );
}

// ---- Events -----------------------------------------------------------------------------------------------------------------
export function EventsPanel() {
  const [eventType, setEventType] = useState('');
  const q = useOrchEvents({ eventType, pageSize: 100 });
  const [open, setOpen] = useState<OrchEvent | null>(null);
  const deliveries = useDeliveries(open?.eventId ?? null);
  const replay = useReplayEvent();
  const canOperate = useCan('orchestration:operate');
  const [replaying, setReplaying] = useState(false);
  const cols: Column<OrchEvent>[] = [
    { key: 'seq', header: '#', align: 'right', sortValue: (e) => e.sequence, cell: (e) => <span className="num text-ink-muted">{e.sequence}</span> },
    { key: 'type', header: 'Event type', sortValue: (e) => e.eventType, cell: (e) => <span className="num text-ink">{e.eventType}</span> },
    { key: 'src', header: 'Source', cell: (e) => <span className="text-xs text-ink-muted">{e.sourceModule} · {e.ingress}</span> },
    { key: 'st', header: 'Status', cell: (e) => <Badge tone={e.status === 'failed' ? 'down' : e.status === 'delivered' ? 'up' : 'neutral'}>{e.status.replace(/_/g, ' ')}</Badge> },
    { key: 'at', header: 'Published (UTC)', sortValue: (e) => e.publishedAt, cell: (e) => when(e.publishedAt) },
  ];
  return (
    <Card>
      <CardBody className="flex flex-wrap items-end gap-3 border-b border-line-soft">
        <label className="flex flex-col gap-1 text-xs text-ink-muted" htmlFor="ev-type">
          Event type (use family.* for a family)
          <input id="ev-type" className="field w-64" value={eventType} onChange={(e) => setEventType(e.target.value.trim())} placeholder="e.g. orchestration.workflow.*" />
        </label>
      </CardBody>
      <QueryState query={q}>
        {(d) => <DataTable caption="Event store" columns={cols} rows={d.data?.items ?? []} rowKey={(e) => e.eventId} onRowClick={setOpen} rowLabel={(e) => `Open event ${e.eventType}`}
          empty={<EmptyState title="No events" description="Events from Modules 06 and 07, n8n and Module 08 itself appear here." />} />}
      </QueryState>
      <Drawer open={!!open} title={open?.eventType ?? ''} subtitle={open?.eventId} onClose={() => setOpen(null)} width="w-[min(96vw,560px)]">
        {open && (
          <div className="flex flex-col gap-3 text-sm">
            <dl>
              <Field label="Source">{open.sourceModule}</Field>
              <Field label="Correlation id">{open.correlationId}</Field>
              <Field label="Occurred (UTC)">{formatDateTime(open.occurredAt)}</Field>
            </dl>
            <pre className="max-h-56 overflow-auto rounded-md bg-base-raised p-2 text-xs">{JSON.stringify({ payload: open.payload, metadata: open.metadata }, null, 2)}</pre>
            <h3 className="eyebrow">Deliveries</h3>
            <ul className="space-y-1 text-xs">
              {(deliveries.data?.data?.items ?? []).map((d) => <li key={d.deliveryId}>{d.subscription} → {d.target}: {d.status} after {d.attempts} attempt(s){d.lastError ? ` — ${d.lastError}` : ''}</li>)}
              {!deliveries.data?.data?.items.length && <li className="text-ink-muted">No subscriber received this event.</li>}
            </ul>
            {canOperate && <Button size="sm" variant="secondary" onClick={() => setReplaying(true)}>Replay to subscribers</Button>}
          </div>
        )}
      </Drawer>
      <ReasonDialog
        open={replaying}
        title="Replay event"
        description="Delivers this event again as a new numbered replay. Risk, approval and execution events cannot be replayed from here; their owning module must issue them again."
        confirmLabel="Replay"
        pending={replay.isPending}
        error={replay.error}
        onClose={() => (setReplaying(false), replay.reset())}
        onSubmit={(reason) => open && replay.mutate({ id: open.eventId, reason }, { onSuccess: (r) => (setReplaying(false), toast({ tone: 'success', title: `Replay ${r.replay} queued`, description: `${r.deliveries} delivery(ies)` })) })}
      />
    </Card>
  );
}

// ---- Failures ---------------------------------------------------------------------------------------------------------------
export function FailuresPanel({ onOpenExecution }: { onOpenExecution: (id: string) => void }) {
  const [status, setStatus] = useState('open');
  const q = useDeadLetters(status);
  const retry = useRetryDeadLetter();
  const resolve = useResolveDeadLetter();
  const canOperate = useCan('orchestration:operate');
  const [act, setAct] = useState<{ dl: DeadLetter; kind: 'retry' | 'resolve' } | null>(null);
  const cols: Column<DeadLetter>[] = [
    { key: 'kind', header: 'Kind', cell: (d) => <Badge tone="neutral">{d.kind}</Badge> },
    { key: 'ref', header: 'Reference', cell: (d) => d.executionId ? <button type="button" className="num text-ink hover:underline" onClick={() => onOpenExecution(d.executionId!)}>{shortId(d.executionId)}</button> : <span className="num text-ink-muted">{shortId(d.eventId)}</span> },
    { key: 'wf', header: 'Workflow', cell: (d) => <span className="text-ink-muted">{d.workflowId ?? '—'}</span> },
    { key: 'cat', header: 'Category', cell: (d) => <span className="text-xs text-down-text">{d.errorCategory}</span> },
    { key: 'why', header: 'Reason', cell: (d) => <span className="text-xs text-ink-muted">{d.reason.slice(0, 120)}</span> },
    { key: 'n', header: 'Attempts', align: 'right', cell: (d) => <span className="num">{d.deliveryCount}</span> },
    { key: 'at', header: 'Failed (UTC)', sortValue: (d) => d.failedAt, cell: (d) => when(d.failedAt) },
    ...(canOperate && status === 'open'
      ? [{ key: 'act', header: '', align: 'right' as const, cell: (d: DeadLetter) => (
          <span className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => setAct({ dl: d, kind: 'retry' })}>Retry</Button>
            <Button size="sm" variant="ghost" onClick={() => setAct({ dl: d, kind: 'resolve' })}>Resolve</Button>
          </span>
        ) }]
      : []),
  ];
  const m = act?.kind === 'retry' ? retry : resolve;
  return (
    <Card>
      <CardHeader title="Dead letters" subtitle="Work that failed for good. Nothing is retried automatically from here; each recovery is a person's decision and is audited." />
      <CardBody className="border-b border-line-soft">
        <label className="flex w-40 flex-col gap-1 text-xs text-ink-muted" htmlFor="dl-status">
          Show
          <select id="dl-status" className="field" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Open</option>
            <option value="retried">Retried</option>
            <option value="resolved">Resolved</option>
          </select>
        </label>
      </CardBody>
      <QueryState query={q}>
        {(d) => <DataTable caption="Dead letters" columns={cols} rows={d.data?.items ?? []} rowKey={(x) => x.id}
          empty={<EmptyState icon={<CheckCircle2 className="h-5 w-5" aria-hidden />} title="No dead letters" description="Nothing needs attention." />} />}
      </QueryState>
      <ReasonDialog
        open={!!act}
        title={act?.kind === 'retry' ? 'Retry dead letter' : 'Resolve dead letter'}
        description={act?.kind === 'retry' ? 'Sends it again. Critical risk, approval and execution events are refused: resolve them and let the owning module re-issue.' : 'Marks it handled without sending anything again.'}
        confirmLabel={act?.kind === 'retry' ? 'Retry' : 'Resolve'}
        pending={m.isPending}
        error={m.error}
        onClose={() => (setAct(null), retry.reset(), resolve.reset())}
        onSubmit={(reason) => {
          if (!act) return;
          const done = { onSuccess: () => (setAct(null), toast({ tone: 'success', title: 'Dead letter updated' })) };
          if (act.kind === 'retry') retry.mutate({ id: act.dl.id, reason }, done);
          else resolve.mutate({ id: act.dl.id, note: reason }, done);
        }}
      />
    </Card>
  );
}

// ---- Integrations -----------------------------------------------------------------------------------------------------------
export function IntegrationsPanel() {
  const q = useIntegrations();
  const test = useTestIntegration();
  const canOperate = useCan('orchestration:operate');
  const cols: Column<Integration>[] = [
    { key: 'n', header: 'Integration', sortValue: (i) => i.integrationId, cell: (i) => <span className="flex flex-col"><span className="text-ink">{i.name}</span><span className="num text-xs text-ink-muted">{i.baseUrl ?? i.kind}</span></span> },
    { key: 's', header: 'Status', sortValue: (i) => i.status, cell: (i) => <HealthBadge status={i.status} /> },
    { key: 'd', header: 'Detail', cell: (i) => <span className="text-xs text-ink-muted">{i.detail ?? '—'}</span> },
    { key: 'l', header: 'Latency', align: 'right', cell: (i) => <span className="num">{i.latencyMs == null ? '—' : `${Math.round(i.latencyMs)} ms`}</span> },
    { key: 'c', header: 'Circuit', cell: (i) => <span className="text-xs text-ink-muted">{i.circuit?.state ?? '—'}</span> },
    { key: 't', header: 'Checked (UTC)', cell: (i) => when(i.lastCheckedAt) },
    ...(canOperate
      ? [{ key: 'a', header: '', align: 'right' as const, cell: (i: Integration) => (
          <Button size="sm" variant="ghost" loading={test.isPending && test.variables === i.integrationId}
            onClick={() => test.mutate(i.integrationId, { onError: (e) => toast({ tone: 'error', title: 'Check failed', description: describeError(e).description }) })}>
            Check now
          </Button>
        ) }]
      : []),
  ];
  return (
    <Card>
      <CardHeader title="Integration health" subtitle="Real checks of n8n, the database, the event store and every AURUM module. Modules that are not built yet show as Unknown." />
      <QueryState query={q}>{(d) => <DataTable caption="Integrations" columns={cols} rows={d.data?.items ?? []} rowKey={(i) => i.integrationId} />}</QueryState>
    </Card>
  );
}

// ---- Configuration ----------------------------------------------------------------------------------------------------------
export function ConfigPanel() {
  const q = useOrchConfig();
  const save = useUpdateConfig();
  const canManage = useCan('orchestration:manage');
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  return (
    <QueryState query={q}>
      {(d) => {
        const c = d.data;
        if (!c) return <EmptyState title="No configuration" />;
        const text = draft ?? JSON.stringify(c.config, null, 2);
        return (
          <div className="flex flex-col gap-5">
            <Card>
              <CardHeader title={`Configuration v${c.version}`} subtitle={`${c.reason} — ${c.createdBy}, ${formatDateTime(c.createdAt)}`} />
              <CardBody className="flex flex-col gap-2">
                <textarea aria-label="Configuration document (JSON)" className="field min-h-[320px] font-mono text-xs" value={text} readOnly={!canManage}
                  onChange={(e) => (setDraft(e.target.value), setParseError(null))} />
                {parseError && <p role="alert" className="text-xs text-down-text">{parseError}</p>}
                {canManage && (
                  <div className="flex gap-2">
                    <Button size="sm" disabled={draft === null} disabledReason="Change the document first" onClick={() => {
                      try {
                        JSON.parse(text);
                        setSaving(true);
                      } catch (e) {
                        setParseError(`Not valid JSON: ${(e as Error).message}`);
                      }
                    }}>Save as new version</Button>
                    <Button size="sm" variant="ghost" disabled={draft === null} disabledReason="Nothing to discard" onClick={() => (setDraft(null), setParseError(null))}>Discard changes</Button>
                  </div>
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Secrets" subtitle={c.note} />
              <CardBody>
                <dl>
                  {c.secrets.map((s) => (
                    <Field key={s.name} label={`${s.name} (${s.source})`}>
                      {s.configured ? <Badge tone="up">Set · {s.fingerprint}</Badge> : <Badge tone="neutral">Not set</Badge>}
                    </Field>
                  ))}
                </dl>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Connection" />
              <CardBody><dl>{Object.entries(c.connection).map(([k, v]) => <Field key={k} label={k}>{String(v ?? '—')}</Field>)}</dl></CardBody>
            </Card>
            <Card>
              <CardHeader title="History" />
              <CardBody>
                <ul className="divide-y divide-line-soft text-xs">{c.history.map((h) => <li key={h.version} className="py-1.5">v{h.version} · {h.createdBy} · {formatDateTime(h.createdAt)} — {h.reason}</li>)}</ul>
              </CardBody>
            </Card>
            <ReasonDialog
              open={saving}
              title="Save configuration"
              description="Creates a new configuration version. Every change is audited; the previous version stays in the history."
              confirmLabel="Save"
              pending={save.isPending}
              error={save.error}
              onClose={() => (setSaving(false), save.reset())}
              onSubmit={(reason) => save.mutate({ config: JSON.parse(text) as Record<string, unknown>, reason, version: c.version },
                { onSuccess: () => (setSaving(false), setDraft(null), toast({ tone: 'success', title: 'Configuration saved' })) })}
            />
          </div>
        );
      }}
    </QueryState>
  );
}

// ---- Audit ------------------------------------------------------------------------------------------------------------------
export function AuditPanel() {
  const [action, setAction] = useState('');
  const q = useOrchAudit(action);
  const cols: Column<AuditRow>[] = [
    { key: 'seq', header: '#', align: 'right', cell: (r) => <span className="num text-ink-muted">{r.seq}</span> },
    { key: 'at', header: 'When (UTC)', cell: (r) => when(r.at) },
    { key: 'who', header: 'Actor', cell: (r) => <span className="text-ink">{r.actor} <span className="text-xs text-ink-muted">({r.actorKind})</span></span> },
    { key: 'act', header: 'Action', cell: (r) => <span className="num">{r.action}</span> },
    { key: 'ent', header: 'Entity', cell: (r) => <span className="text-xs text-ink-muted">{r.entityType} {r.entityId.slice(0, 36)}</span> },
    { key: 'out', header: 'Outcome', cell: (r) => <Badge tone={r.outcome === 'refused' ? 'warn' : 'neutral'}>{r.outcome}</Badge> },
  ];
  return (
    <Card>
      <CardBody className="flex flex-wrap items-end justify-between gap-3 border-b border-line-soft">
        <label className="flex flex-col gap-1 text-xs text-ink-muted" htmlFor="au-action">
          Action (use prefix.* to filter)
          <input id="au-action" className="field w-56" value={action} onChange={(e) => setAction(e.target.value.trim())} placeholder="e.g. security.*" />
        </label>
        {q.data?.data && (
          <Badge tone={q.data.data.chain.valid ? 'up' : 'down'}>
            Hash chain {q.data.data.chain.valid ? 'intact' : 'BROKEN'} · {q.data.data.chain.rows} rows
          </Badge>
        )}
      </CardBody>
      <QueryState query={q}>{(d) => <DataTable caption="Module 08 audit log" columns={cols} rows={d.data?.items ?? []} rowKey={(r) => String(r.seq)} dense />}</QueryState>
    </Card>
  );
}
