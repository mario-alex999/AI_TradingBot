import { CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartCard } from '@/components/analytics/ChartCard';
import { axisProps, gridProps, legendProps, SERIES, tooltipProps } from '@/components/analytics/chartStyles';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataTable, type Column } from '@/components/common/DataTable';
import type { CotDivergence, CotDivergenceWindow } from '@/types/cot';
import { formatCompact } from '@/utils/format';
import { humanize, signed } from './cotFormat';

const SIGNAL_TEXT: Record<string, string> = {
  bearish_divergence: 'Price rose while the group reduced its net position',
  bullish_divergence: 'Price fell while the group increased its net position',
  unconfirmed_high: 'Price new high not confirmed by positioning',
  unconfirmed_low: 'Price new low while net-short positioning weakened',
};

/** G. Price vs positioning, with the COT report dates and price dates used for every comparison. */
export function CotDivergencePanel({ data, demo }: { data: CotDivergence | null | undefined; demo: boolean }) {
  if (demo || !data) {
    return (
      <Card>
        <CardHeader title="Price and positioning divergence" />
        <CardBody className="text-xs text-ink-muted">
          {demo ? 'Not available in demo mode: divergence uses stored CFTC reports and Module 01 prices.' : 'No divergence analysis available.'}
        </CardBody>
      </Card>
    );
  }
  const rows = data.series.filter((r) => r.priceClose != null);
  const columns: Column<CotDivergenceWindow>[] = [
    { key: 'window', header: 'COT reports compared', cell: (r) => <span className="num">{r.windowStartReportDate} → {r.reportDate}</span> },
    { key: 'prices', header: 'Price dates', cell: (r) => <span className="num text-neutral">{r.windowStartPriceDate} → {r.priceDate}</span> },
    { key: 'p', header: 'Price Δ', align: 'right', cell: (r) => `${r.priceChangePct > 0 ? '+' : ''}${r.priceChangePct.toFixed(2)}%` },
    { key: 'n', header: 'Net Δ', align: 'right', cell: (r) => signed(r.netPositionChange) },
    { key: 's', header: 'Signals', cell: (r) => <span className="flex flex-wrap gap-1">{r.signals.map((s) => <Badge key={s} tone="warn">{humanize(s)}</Badge>)}</span> },
  ];
  const latest = data.latest;
  return (
    <div className="flex flex-col gap-4">
      <ChartCard
        title={`Price vs ${data.categoryLabel} net position`}
        subtitle={`${data.priceTimeframe} close on each Tuesday report date (Module 01) vs COT net position. Price data ${data.priceCoverage.from ?? '--'} to ${data.priceCoverage.to ?? '--'}.`}
        empty={!data.available || rows.length === 0}
        emptyTitle="Not enough overlapping data"
        emptyDescription={data.reason ?? undefined}
        height={300}
      >
        <ResponsiveContainer>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="reportDate" {...axisProps} tickFormatter={(d: string) => d.slice(2, 7)} minTickGap={28} />
            <YAxis yAxisId="net" {...axisProps} tickFormatter={(v: number) => formatCompact(v)} width={56} />
            <YAxis yAxisId="price" orientation="right" {...axisProps} domain={['auto', 'auto']} width={64} />
            <Tooltip {...tooltipProps} labelFormatter={(l: string) => `COT report ${l}`} />
            <Legend {...legendProps} />
            <Line yAxisId="net" type="monotone" dataKey="netPosition" name={`${data.categoryLabel} net`} stroke={SERIES.nonCommercial} dot={false} strokeWidth={1.5} />
            <Line yAxisId="price" type="monotone" dataKey="priceClose" name="Price (close)" stroke={SERIES.commercial} dot={false} strokeWidth={1.2} />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartCard>
      <Card>
        <CardHeader
          title="Divergence assessment"
          subtitle={`Last ${data.windowReports} report intervals. ${data.method}`}
          actions={latest && <Badge tone={latest.signals.length ? 'warn' : 'neutral'}>{latest.signals.length ? `${latest.signals.length} signal(s)` : 'No divergence'}</Badge>}
        />
        <CardBody className="flex flex-col gap-3 text-xs">
          {latest ? (
            <p className="text-ink-muted">
              Latest window: COT {latest.windowStartReportDate} → {latest.reportDate} (prices {latest.windowStartPriceDate} → {latest.priceDate}); price{' '}
              {latest.priceChangePct > 0 ? '+' : ''}
              {latest.priceChangePct.toFixed(2)}%, net {signed(latest.netPositionChange)}.{' '}
              {latest.signals.length ? latest.signals.map((s) => SIGNAL_TEXT[s]).join('; ') + '.' : 'Price and positioning moved consistently.'}
            </p>
          ) : (
            <p className="text-ink-muted">{data.reason}</p>
          )}
          <p className="text-neutral">A divergence describes disagreement between price and positioning; it is not evidence of an imminent reversal.</p>
        </CardBody>
        <DataTable columns={columns} rows={[...data.signals].reverse().slice(0, 12)} rowKey={(r) => r.reportDate} dense empty="No divergence signals in the stored history." caption="Historical divergence signals" />
      </Card>
    </div>
  );
}
