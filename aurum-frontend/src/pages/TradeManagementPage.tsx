import { useState } from 'react';
import { Lock } from 'lucide-react';
import { differenceInMinutes } from 'date-fns';
import { PageHeader } from '@/components/common/PageHeader';
import { Card } from '@/components/common/Card';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { DataTable, type Column } from '@/components/common/DataTable';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { EmptyState } from '@/components/common/EmptyState';
import { QueryState } from '@/components/common/QueryState';
import { Badge } from '@/components/common/Badge';
import { useOpenTrades, useTradeHistory } from '@/hooks/queries';
import { formatCurrency, formatDateTime, formatDuration, formatNumber, PLACEHOLDER } from '@/utils/format';
import type { Position } from '@/types/trading';

type Tab = 'open' | 'pending' | 'closed';

function columns(kind: Tab): Column<Position>[] {
  const price = (v: number | null | undefined) => <span className="num">{v == null ? PLACEHOLDER : formatNumber(v, 5)}</span>;
  const base: Column<Position>[] = [
    { key: 'ticket', header: 'Ticket', cell: (r) => <span className="num text-neutral">{r.ticket}</span> },
    { key: 'inst', header: 'Instrument', sortValue: (r) => r.instrument, cell: (r) => <span className="num font-semibold text-ink">{r.instrument}</span> },
    { key: 'side', header: 'Side', cell: (r) => <Badge tone={r.side === 'buy' ? 'up' : 'down'}>{r.side}</Badge> },
    { key: 'vol', header: 'Volume', align: 'right', sortValue: (r) => r.volume, cell: (r) => <span className="num">{r.volume.toFixed(2)}</span> },
    { key: 'entry', header: 'Entry', align: 'right', cell: (r) => price(r.entryPrice) },
    { key: 'sl', header: 'Stop loss', align: 'right', cell: (r) => price(r.stopLoss) },
    { key: 'tp', header: 'Take profit', align: 'right', cell: (r) => price(r.takeProfit) },
  ];
  if (kind === 'closed')
    return [
      ...base,
      { key: 'close', header: 'Close', align: 'right', cell: (r) => price(r.closePrice) },
      {
        key: 'pnl',
        header: 'Realised P/L',
        align: 'right',
        sortValue: (r) => r.realisedPnl ?? null,
        cell: (r) => <span className={(r.realisedPnl ?? 0) >= 0 ? 'num text-up' : 'num text-down-text'}>{formatCurrency(r.realisedPnl)}</span>,
      },
      { key: 'closed', header: 'Closed (UTC)', cell: (r) => <span className="num text-xs text-ink-muted">{formatDateTime(r.closedAt ?? null)}</span> },
    ];
  return [
    ...base,
    ...(kind === 'open'
      ? [
          { key: 'cur', header: 'Current', align: 'right' as const, cell: (r: Position) => price(r.currentPrice) },
          {
            key: 'pnl',
            header: 'Floating P/L',
            align: 'right' as const,
            sortValue: (r: Position) => r.floatingPnl,
            cell: (r: Position) => <span className={(r.floatingPnl ?? 0) >= 0 ? 'num text-up' : 'num text-down-text'}>{formatCurrency(r.floatingPnl)}</span>,
          },
        ]
      : []),
    {
      key: 'dur',
      header: 'Duration',
      align: 'right',
      sortValue: (r) => r.openedAt,
      cell: (r) => <span className="num text-ink-muted">{formatDuration(differenceInMinutes(new Date(), new Date(r.openedAt)) * 60_000)}</span>,
    },
  ];
}

export default function TradeManagementPage() {
  const [tab, setTab] = useState<Tab>('open');
  const open = useOpenTrades();
  const history = useTradeHistory();
  const q = tab === 'closed' ? history : open;
  const rows = (q.data?.data ?? []).filter((p) => (tab === 'open' ? p.kind === 'position' : tab === 'pending' ? p.kind === 'pending' : true));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Trading"
        title="Trade Management"
        description="Positions, pending orders and history reported by the backend from the MT5 account."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <div className="flex items-center gap-2 rounded-card border border-line bg-base-card px-4 py-3 text-xs text-ink-muted">
        <Lock className="h-4 w-4 shrink-0 text-gold" aria-hidden />
        Read-only. Order placement and position modification are not available in this release; future trading actions will require backend authorisation and human
        approval.
      </div>
      <SegmentedControl
        label="Trade view"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'open', label: 'Open positions' },
          { value: 'pending', label: 'Pending orders' },
          { value: 'closed', label: 'Closed trades / history' },
        ]}
      />
      <Card>
        <QueryState query={q}>
          {() => (
            <DataTable
              caption={tab}
              columns={columns(tab)}
              rows={rows}
              rowKey={(r) => r.ticket}
              empty={
                <EmptyState
                  title={tab === 'open' ? 'No open positions' : tab === 'pending' ? 'No pending orders' : 'No trade history'}
                  description={q.data?.source === 'demo' ? 'Account data is never simulated. Connect the backend to see MT5 positions.' : undefined}
                />
              }
            />
          )}
        </QueryState>
      </Card>
    </div>
  );
}
