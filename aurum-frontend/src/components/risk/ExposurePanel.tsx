import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { PieChart } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataTable, type Column } from '@/components/common/DataTable';
import { EmptyState } from '@/components/common/EmptyState';
import { ChartCard } from '@/components/analytics/ChartCard';
import { axisProps, gridProps, tooltipProps } from '@/components/analytics/chartStyles';
import { UsageBar } from './UsageBar';
import { useRiskPortfolio } from '@/hooks/useRisk';
import type { Correlation, ExposureRow, RiskBucket } from '@/types/risk';
import { describeError } from '@/utils/errors';
import { formatCompact, formatCurrency, formatNumber, formatPercent } from '@/utils/format';
import { STATUS_LABEL, STATUS_TONE } from '@/utils/riskLabels';

function bucketRows(b: RiskBucket) {
  return Object.entries(b).map(([name, v]) => ({ name, value: v.riskPct ?? 0 }));
}

function BucketChart({ title, bucket, limit }: { title: string; bucket: RiskBucket; limit: number }) {
  const data = bucketRows(bucket);
  return (
    <ChartCard title={title} empty={data.length === 0} emptyTitle="No open risk" emptyDescription="No open positions or pending approvals.">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid {...gridProps} horizontal={false} vertical />
          <XAxis type="number" {...axisProps} domain={[0, (max: number) => Math.max(max, limit) * 1.1]} tickFormatter={(v: number) => `${v.toFixed(1)}%`} />
          <YAxis type="category" dataKey="name" {...axisProps} width={72} />
          <Tooltip {...tooltipProps} formatter={(v: number) => `${v.toFixed(2)}% of equity`} />
          <ReferenceLine x={limit} stroke="rgb(var(--c-down))" strokeDasharray="4 3" label={{ value: 'limit', fontSize: 10, fill: 'rgb(var(--c-down-text))', position: 'top' }} />
          <Bar dataKey="value" fill="rgb(var(--c-gold))" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

const POSITION_COLUMNS: Column<ExposureRow>[] = [
  { key: 'symbol', header: 'Instrument', cell: (r) => r.symbol },
  { key: 'side', header: 'Side', cell: (r) => <Badge tone={r.side === 'BUY' ? 'up' : 'down'}>{r.side}</Badge> },
  { key: 'kind', header: 'Type', cell: (r) => (r.kind === 'position' ? 'Open position' : 'Approved, pending') },
  { key: 'strategy', header: 'Strategy', cell: (r) => r.strategy ?? '—' },
  { key: 'risk', header: 'Risk at stop', align: 'right', sortValue: (r) => r.risk, cell: (r) => (r.risk == null ? <Badge tone="down">No stop</Badge> : formatCurrency(r.risk)) },
  { key: 'riskPct', header: '% equity', align: 'right', sortValue: (r) => r.riskPct, cell: (r) => formatPercent(r.riskPct) },
];

const CORR_COLUMNS: Column<Correlation>[] = [
  { key: 'pair', header: 'Pair', cell: (c) => `${c.a} / ${c.b}` },
  { key: 'coef', header: 'Correlation', align: 'right', sortValue: (c) => c.coefficient, cell: (c) => formatNumber(c.coefficient, 2) },
  { key: 'samples', header: 'Samples', align: 'right', cell: (c) => `${c.samples} ${c.timeframe}` },
  { key: 'reliable', header: 'Reliability', cell: (c) => (c.reliable ? <Badge tone="up">Reliable</Badge> : <Badge tone="warn">{c.reason ?? 'Unreliable'}</Badge>) },
];

/** Section D: portfolio exposure, including risk reserved by approvals that are not yet filled. */
export function ExposurePanel() {
  const q = useRiskPortfolio();
  const p = q.data?.data;
  if (q.isError) {
    const e = describeError(q.error);
    return <EmptyState title={e.title} description={e.description} />;
  }
  if (!p) return <EmptyState title="Exposure not available" description="Exposure is computed by the backend risk engine. Demo mode shows none." icon={<PieChart className="h-5 w-5" />} />;

  const ccyRows = Object.entries(p.currencyExposure).map(([ccy, v]) => ({ ccy, v }));
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          icon={<PieChart className="h-4 w-4" />}
          title="Portfolio risk"
          subtitle="Risk at the stop-loss of open positions plus approvals that have not expired."
          actions={<Badge tone={STATUS_TONE[p.status]}>{STATUS_LABEL[p.status]}</Badge>}
        />
        <CardBody className="flex flex-col gap-4">
          <div className="grid gap-5 md:grid-cols-2">
            <UsageBar
              label="Total open risk"
              value={p.totalOpenRiskPct}
              limit={p.maxTotalOpenRiskPct}
              format={(v) => formatPercent(v)}
              note={`${formatCurrency(p.totalOpenRisk)} at the stops${p.bounded ? '' : ' · plus unbounded positions'}`}
            />
            <div className="flex flex-col gap-1 text-xs text-ink-muted">
              <span>
                Per-instrument limit <span className="num text-ink">{formatPercent(p.maxSymbolRiskPct)}</span> · per asset class{' '}
                <span className="num text-ink">{formatPercent(p.maxAssetClassRiskPct)}</span> · correlated{' '}
                <span className="num text-ink">{formatPercent(p.maxCorrelatedRiskPct)}</span>
              </span>
            </div>
          </div>
          {!p.bounded && (
            <p role="alert" className="rounded-md bg-down-faint p-2 text-xs text-down-text">
              {p.unboundedPositions.length} open position(s) have no stop-loss, so total risk is unbounded and new trades are blocked.
            </p>
          )}
          {p.concentrationWarnings.map((w) => (
            <p key={w} className="text-xs text-warn">
              {w}
            </p>
          ))}
        </CardBody>
      </Card>
      <div className="grid gap-5 xl:grid-cols-2">
        <BucketChart title="Risk by instrument" bucket={p.byInstrument} limit={p.maxSymbolRiskPct} />
        <BucketChart title="Risk by asset class" bucket={p.byAssetClass} limit={p.maxAssetClassRiskPct} />
      </div>
      <Card>
        <CardHeader title="Positions and pending approvals" subtitle={`${p.positions.length} item(s)`} />
        <CardBody>
          <DataTable columns={POSITION_COLUMNS} rows={p.positions} rowKey={(r) => `${r.kind}-${r.ref}`} dense empty="No open positions or pending approvals." />
        </CardBody>
      </Card>
      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader title="Correlations" subtitle="Pearson on log returns over common bars. Unreliable pairs are treated as correlated." />
          <CardBody>
            <DataTable columns={CORR_COLUMNS} rows={p.correlations} rowKey={(c) => `${c.a}-${c.b}`} dense empty="Correlations appear once two or more instruments carry risk." />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Currency exposure" subtitle="Net notional by currency (long positive, short negative)." />
          <CardBody>
            {ccyRows.length === 0 ? (
              <p className="text-xs text-neutral">No exposure.</p>
            ) : (
              <dl>
                {ccyRows.map(({ ccy, v }) => (
                  <div key={ccy} className="flex justify-between border-b border-line-soft py-1.5 text-xs last:border-0">
                    <dt className="text-neutral">{ccy}</dt>
                    <dd className={v < 0 ? 'num text-down-text' : 'num text-ink'}>{formatCompact(v)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
