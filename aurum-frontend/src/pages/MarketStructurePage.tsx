import { useState } from 'react';
import { Activity } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { PageHeader } from '@/components/common/PageHeader';
import { Select } from '@/components/common/Select';
import { Badge, type Tone } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { useInstruments, useMarketStructure } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import { formatDateTime, formatNumber } from '@/utils/format';
import type { Bias, StructureEvent, TimeframeStructure } from '@/types/analysis';
import type { Timeframe } from '@/types/market';

const BIAS: Record<Bias, { label: string; tone: Tone }> = {
  bullish: { label: 'Bullish', tone: 'up' },
  bearish: { label: 'Bearish', tone: 'down' },
  ranging: { label: 'Ranging', tone: 'info' },
  awaiting: { label: 'Awaiting confirmation', tone: 'neutral' },
};

const EVENT_ROWS: { type: StructureEvent['type']; label: string }[] = [
  { type: 'HH', label: 'Higher highs' },
  { type: 'HL', label: 'Higher lows' },
  { type: 'LH', label: 'Lower highs' },
  { type: 'LL', label: 'Lower lows' },
  { type: 'BOS', label: 'Break of structure' },
  { type: 'CHOCH', label: 'Change of character' },
  { type: 'displacement', label: 'Displacement' },
  { type: 'liquidity_sweep', label: 'Liquidity sweeps' },
  { type: 'fvg', label: 'Fair value gaps' },
  { type: 'order_block', label: 'Order blocks' },
];

export default function MarketStructurePage() {
  const instruments = useInstruments();
  const [symbol, setSymbol] = useState('EURUSD');
  const q = useMarketStructure(symbol);
  const report = q.data?.data ?? null;
  const byTf = new Map<Timeframe, TimeframeStructure>((report?.timeframes ?? []).map((t) => [t.timeframe, t]));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="Market Structure"
        description="Multi-timeframe swing structure, breaks and liquidity events produced by the Market Structure Agent."
        actions={
          <>
            <DataSourceBadge source={q.data?.source} />
            <Select
              label="Instrument"
              hideLabel
              value={symbol}
              onChange={setSymbol}
              options={(instruments.data?.data ?? DEFAULT_INSTRUMENTS).map((i) => ({ value: i.symbol, label: i.symbol }))}
              className="w-36"
            />
          </>
        }
      />

      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <Card>
          <CardHeader icon={<Activity className="h-4 w-4" />} title="Structure summary" subtitle={report ? `Generated ${formatDateTime(report.generatedAt)} UTC` : 'No analysis received'} />
          <CardBody>
            {q.isPending ? (
              <SkeletonRows rows={5} />
            ) : q.isError ? (
              <ErrorState error={q.error} compact onRetry={() => void q.refetch()} />
            ) : (
              <dl className="flex flex-col">
                <div className="flex justify-between border-b border-line-soft pb-2">
                  <dt className="eyebrow">Instrument</dt>
                  <dd className="num text-sm font-semibold text-ink">{symbol}</dd>
                </div>
                {TIMEFRAMES.map((tf) => {
                  const b = BIAS[byTf.get(tf)?.bias ?? 'awaiting'];
                  return (
                    <div key={tf} className="flex items-center justify-between border-b border-line-soft py-2.5 last:border-0">
                      <dt className="num text-sm text-ink-muted">{tf}</dt>
                      <dd>
                        <Badge tone={b.tone}>{b.label}</Badge>
                      </dd>
                    </div>
                  );
                })}
              </dl>
            )}
            {!report && !q.isPending && (
              <p className="mt-3 text-2xs text-neutral">Every timeframe shows “Awaiting confirmation” until the analysis backend publishes structure for {symbol}.</p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Structure events by timeframe" subtitle="Counts of detected events in the current analysis window." />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <caption className="sr-only">Structure events by timeframe</caption>
              <thead>
                <tr className="border-b border-line">
                  <th scope="col" className="px-3 py-2.5 text-left text-2xs font-semibold uppercase tracking-wider text-neutral">
                    Event
                  </th>
                  {TIMEFRAMES.map((tf) => (
                    <th key={tf} scope="col" className="num px-3 py-2.5 text-right text-2xs font-semibold text-neutral">
                      {tf}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {EVENT_ROWS.map((row) => (
                  <tr key={row.type} className="border-b border-line-soft last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-normal text-ink-muted">
                      {row.label}
                    </th>
                    {TIMEFRAMES.map((tf) => {
                      const s = byTf.get(tf);
                      return (
                        <td key={tf} className="num px-3 py-2 text-right text-ink">
                          {s ? s.events.filter((e) => e.type === row.type).length : '--'}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                <tr>
                  <th scope="row" className="px-3 py-2 text-left font-normal text-ink-muted">
                    Premium / discount (equilibrium)
                  </th>
                  {TIMEFRAMES.map((tf) => {
                    const pd = byTf.get(tf)?.premiumDiscount;
                    return (
                      <td key={tf} className="num px-3 py-2 text-right text-ink">
                        {pd ? formatNumber(pd.equilibrium, 5) : '--'}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card className="border-dashed">
        <CardHeader
          title="Illustrative layout"
          subtitle="Interface example only. This is NOT analysis and does not reflect any market."
          actions={<Badge tone="warn">Example · not real</Badge>}
        />
        <CardBody>
          <dl className="num grid max-w-sm gap-1 text-xs text-neutral" aria-label="Illustrative example">
            {[
              ['INSTRUMENT', 'EURUSD'],
              ['D1', 'Bullish'],
              ['H4', 'Bullish'],
              ['H1', 'Bearish'],
              ['M15', 'Awaiting confirmation'],
              ['M5', 'Awaiting confirmation'],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </CardBody>
      </Card>
    </div>
  );
}
