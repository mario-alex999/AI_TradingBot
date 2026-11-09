import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Lock, RefreshCw } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { MetricCard } from '@/components/common/MetricCard';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { DataTable, type Column } from '@/components/common/DataTable';
import { EmptyState } from '@/components/common/EmptyState';
import { QueryState } from '@/components/common/QueryState';
import { Button } from '@/components/common/Button';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Countdown, DirectionBadge, RiskBadge, StatusBadge } from '@/components/approvals/ApprovalBits';
import { STATUS_LABEL, priceDigits } from '@/utils/approvals';
import { useApprovalDashboard, useApprovalList, usePendingApprovals, useSyncIdeas } from '@/hooks/useApprovals';
import { canServer, useAuthStore } from '@/stores/authStore';
import { toast } from '@/stores/toastStore';
import { describeError } from '@/utils/errors';
import { formatDateTime, formatNumber, formatPercent } from '@/utils/format';
import type { ApprovalRequest, ApprovalStatus } from '@/types/approvals';

type View = 'pending' | 'all';

function columns(view: View): Column<ApprovalRequest>[] {
  return [
    { key: 'sym', header: 'Instrument', sortValue: (r) => r.symbol, cell: (r) => <span className="num font-semibold text-ink">{r.symbol}</span> },
    { key: 'dir', header: 'Side', cell: (r) => <DirectionBadge direction={r.direction} /> },
    { key: 'tf', header: 'TF', cell: (r) => <span className="num text-ink-muted">{r.timeframe}</span> },
    { key: 'strat', header: 'Strategy', cell: (r) => <span className="text-ink-muted">{r.strategy.replace(/_/g, ' ')}</span> },
    { key: 'entry', header: 'Entry', align: 'right', cell: (r) => <span className="num">{formatNumber(r.entry, priceDigits(r.symbol))}</span> },
    { key: 'size', header: 'Size', align: 'right', sortValue: (r) => r.positionSize, cell: (r) => <span className="num">{r.positionSize == null ? '—' : formatNumber(r.positionSize, 2)}</span> },
    { key: 'riskpct', header: 'Risk', align: 'right', sortValue: (r) => r.riskPct, cell: (r) => <span className="num">{formatPercent(r.riskPct)}</span> },
    { key: 'risk', header: 'Risk check', cell: (r) => <RiskBadge decision={r.riskDecision} /> },
    { key: 'status', header: 'Status', sortValue: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
    view === 'pending'
      ? { key: 'exp', header: 'Expires in', align: 'right', sortValue: (r) => r.expiresAt, cell: (r) => <Countdown expiresAt={r.expiresAt} /> }
      : { key: 'created', header: 'Created (UTC)', sortValue: (r) => r.createdAt, cell: (r) => <span className="num text-xs text-ink-muted">{formatDateTime(r.createdAt)}</span> },
  ];
}

export function SignInRequired({ what }: { what: string }) {
  return (
    <Card>
      <EmptyState
        icon={<Lock className="h-5 w-5" aria-hidden />}
        title="Sign in with a backend account"
        description={`${what} need a real identity, so every decision is recorded against a person. The demo workspace can browse the rest of AURUM. Sign out and sign in with your AURUM account.`}
      />
    </Card>
  );
}

export default function ApprovalsPage() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [view, setView] = useState<View>('pending');
  const [status, setStatus] = useState('');
  const [symbol, setSymbol] = useState('');
  const [page, setPage] = useState(1);
  const dash = useApprovalDashboard();
  const pending = usePendingApprovals();
  const list = useApprovalList({ status, symbol: symbol.trim().toUpperCase(), page, pageSize: 50 });
  const sync = useSyncIdeas();

  if (user?.mode !== 'server') {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader eyebrow="Trading" title="Trade Approvals" description="Every trade idea needs an explicit human decision before anything is sent for execution." />
        <SignInRequired what="Trade approvals" />
      </div>
    );
  }

  const stats = dash.data?.data?.statistics;
  const counts = dash.data?.data?.counts;
  const q = view === 'pending' ? pending : list;
  const rows = q.data?.data?.items ?? [];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Trading"
        title="Trade Approvals"
        description="Trade ideas wait here for your decision. Nothing is executed without an explicit approval, and approval only hands the trade to Module 09, which checks the risk again."
        actions={
          <>
            <DataSourceBadge source={dash.data?.source} fetchedAt={dash.data?.fetchedAt} />
            {canServer(user, 'approvals:create') && (
              <Button
                size="sm"
                icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden />}
                loading={sync.isPending}
                onClick={() =>
                  sync.mutate(undefined, {
                    onSuccess: (r) => toast({ tone: 'success', title: 'Decision engine checked', description: `${r.ingested} new request(s), ${r.closed} closed.` }),
                    onError: (e) => toast({ tone: 'error', title: 'Could not check for new ideas', description: describeError(e).description }),
                  })
                }
              >
                Check for new ideas
              </Button>
            )}
          </>
        }
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Waiting for you" value={<span className="num">{(counts?.PENDING ?? 0) + (counts?.UNDER_REVIEW ?? 0)}</span>} />
        <MetricCard label="Approved" value={<span className="num">{stats?.approved ?? '—'}</span>} />
        <MetricCard label="Rejected" value={<span className="num">{stats?.rejected ?? '—'}</span>} />
        <MetricCard label="Expired" value={<span className="num">{stats?.expired ?? '—'}</span>} info="Requests that reached their time limit without a decision. Nothing was executed." />
        <MetricCard label="Executed" value={<span className="num">{stats?.executed ?? '—'}</span>} info="Reported by Module 09 after it placed the order." />
        <MetricCard
          label="Median decision time"
          value={<span className="num">{stats?.medianDecisionSeconds == null ? '—' : `${Math.round(stats.medianDecisionSeconds / 60)} min`}</span>}
        />
      </div>
      <SegmentedControl
        label="Approval view"
        value={view}
        onChange={(v) => {
          setView(v);
          setPage(1);
        }}
        options={[
          { value: 'pending', label: 'Waiting for decision' },
          { value: 'all', label: 'All requests / history' },
        ]}
      />
      <Card>
        {view === 'all' && (
          <CardBody className="flex flex-wrap items-end gap-3 border-b border-line-soft">
            <div className="flex flex-col gap-1">
              <label htmlFor="f-status" className="text-xs text-ink-muted">
                Status
              </label>
              <select id="f-status" className="field" value={status} onChange={(e) => (setStatus(e.target.value), setPage(1))}>
                <option value="">Any</option>
                {(Object.keys(STATUS_LABEL) as ApprovalStatus[]).map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABEL[s]}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="f-symbol" className="text-xs text-ink-muted">
                Instrument
              </label>
              <input id="f-symbol" className="field w-32" placeholder="e.g. EURUSD" value={symbol} onChange={(e) => (setSymbol(e.target.value), setPage(1))} />
            </div>
          </CardBody>
        )}
        <QueryState query={q}>
          {() => (
            <DataTable
              caption={view === 'pending' ? 'Requests waiting for a decision' : 'All approval requests'}
              columns={columns(view)}
              rows={rows}
              rowKey={(r) => r.approvalId}
              onRowClick={(r) => navigate(`/approvals/${r.approvalId}`)}
              rowLabel={(r) => `Open ${r.direction} ${r.symbol} request`}
              empty={
                <EmptyState
                  title={view === 'pending' ? 'Nothing is waiting for a decision' : 'No approval requests yet'}
                  description="New trade ideas from the decision engine appear here with their evidence and risk assessment."
                />
              }
            />
          )}
        </QueryState>
        {view === 'all' && (list.data?.data?.total ?? 0) > 50 && (
          <CardBody className="flex items-center justify-between border-t border-line-soft text-xs text-ink-muted">
            <span>
              Page {page} of {Math.ceil((list.data?.data?.total ?? 0) / 50)}
            </span>
            <span className="flex gap-2">
              <Button size="sm" variant="ghost" disabled={page <= 1} disabledReason="First page" onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <Button size="sm" variant="ghost" disabled={page * 50 >= (list.data?.data?.total ?? 0)} disabledReason="Last page" onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </span>
          </CardBody>
        )}
      </Card>
      {(dash.data?.data?.recentHandoffs?.length ?? 0) > 0 && (
        <Card>
          <CardHeader title="Recent execution handoffs" subtitle="Approved trades handed to Module 09. Module 09 is not built yet, so they wait in its queue." />
          <CardBody>
            <ul className="divide-y divide-line-soft text-sm">
              {dash.data?.data?.recentHandoffs.map((h) => (
                <li key={h.executionRequestId} className="flex items-center justify-between gap-3 py-2">
                  {h.approvalId ? (
                    <Link to={`/approvals/${h.approvalId}`} className="num text-ink hover:underline">
                      {h.executionRequestId.slice(0, 8)}
                    </Link>
                  ) : (
                    <span className="num text-ink">{h.executionRequestId.slice(0, 8)}</span>
                  )}
                  <span className="text-xs text-ink-muted">{h.kind}</span>
                  <span className="text-xs text-ink-muted">{h.status}</span>
                  <span className="num text-xs text-neutral">{formatDateTime(h.createdAt)}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
