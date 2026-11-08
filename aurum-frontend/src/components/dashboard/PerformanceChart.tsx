import { useMemo, useState } from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Skeleton } from '@/components/common/Skeleton';
import { ErrorState } from '@/components/common/ErrorState';
import { axisProps, gridProps, tooltipProps } from '@/components/analytics/chartStyles';
import { useCandlesMany, useInstruments } from '@/hooks/queries';
import { chartPalette } from '@/theme/theme';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { formatInZone } from '@/utils/format';
import { cn } from '@/utils/cn';
import type { Timeframe } from '@/types/market';

type Range = '1D' | '1W' | '1M';
const RANGE: Record<Range, { tf: Timeframe; bars: number; label: Intl.DateTimeFormatOptions }> = {
  '1D': { tf: 'M15', bars: 96, label: { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' } },
  '1W': { tf: 'H1', bars: 120, label: { weekday: 'short', day: '2-digit' } },
  '1M': { tf: 'H4', bars: 180, label: { day: '2-digit', month: 'short' } },
};
const MAX_LINES = 4;
const DEFAULT_SYMBOLS = DEFAULT_INSTRUMENTS.map((i) => i.symbol).slice(0, MAX_LINES);

/**
 * "How have my instruments moved?" — % change from the start of the window, so
 * instruments with very different prices share one scale. Click a legend item to
 * hide/show it; hover one to highlight its line.
 */
export function PerformanceChart() {
  const [range, setRange] = useState<Range>('1W');
  const [hidden, setHidden] = useState<string[]>([]);
  const [hover, setHover] = useState<string | null>(null);
  const { tf, bars, label } = RANGE[range];
  // Chart the instruments the backend actually serves, so no request is made for a symbol it does not have.
  const instruments = useInstruments();
  const served = (instruments.data?.data ?? []).filter((i) => i.enabled).map((i) => i.symbol);
  // Wait for the list; fall back to the configured defaults only if it cannot be loaded or is empty.
  const SYMBOLS = served.length ? served.slice(0, MAX_LINES) : instruments.isPending ? [] : DEFAULT_SYMBOLS;
  const pal = chartPalette();
  const lineColours = [pal.green1, pal.green2, pal.dark ? '#D9A94A' : '#B8892E', pal.grey1];
  const COLOURS: Record<string, string> = Object.fromEntries(SYMBOLS.map((s, i) => [s, lineColours[i % lineColours.length]!]));
  const queries = useCandlesMany(SYMBOLS, tf, bars);
  const pending = queries.every((q) => q.isPending);
  // One missing instrument (e.g. not configured on the backend) only drops its own line;
  // the chart fails only when no instrument has data.
  const failed = queries.every((q) => q.isError) ? queries.find((q) => q.isError) : undefined;
  const missing = SYMBOLS.filter((_, i) => queries[i]!.isError || (queries[i]!.isSuccess && (queries[i]!.data?.data.length ?? 0) === 0));

  const { rows, last } = useMemo(() => {
    const byTime = new Map<number, Record<string, number>>();
    const lastPct: Record<string, number> = {};
    queries.forEach((q, i) => {
      const sym = SYMBOLS[i]!;
      const candles = q.data?.data ?? [];
      const base = candles[0]?.close;
      if (!base) return;
      for (const c of candles) {
        const row = byTime.get(c.time) ?? { time: c.time };
        row[sym] = Number((((c.close - base) / base) * 100).toFixed(3));
        byTime.set(c.time, row);
        lastPct[sym] = row[sym]!;
      }
    });
    return { rows: [...byTime.values()].sort((a, b) => a.time! - b.time!), last: lastPct };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [SYMBOLS.join(','), queries.map((q) => q.dataUpdatedAt).join(',')]);

  const toggle = (s: string) => setHidden((h) => (h.includes(s) ? h.filter((x) => x !== s) : [...h, s]));

  return (
    <Card className="flex flex-col">
      <CardHeader
        title="Market performance"
        subtitle="Change from the start of the period, so every instrument shares one scale."
        actions={
          <>
            <DataSourceBadge source={queries[0]?.data?.source} />
            <SegmentedControl label="Period" value={range} options={['1D', '1W', '1M'] as const} onChange={setRange} />
          </>
        }
      />
      <CardBody className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Show or hide instruments">
          {SYMBOLS.map((s) => {
            const off = hidden.includes(s);
            const pct = last[s];
            if (missing.includes(s)) {
              return (
                <span key={s} className="flex items-center gap-2 rounded-full border border-dashed border-line-control px-3 py-1 text-xs text-neutral" title="No stored candles for this instrument">
                  {s} · no data
                </span>
              );
            }
            return (
              <button
                key={s}
                type="button"
                aria-pressed={!off}
                onClick={() => toggle(s)}
                onMouseEnter={() => setHover(s)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(s)}
                onBlur={() => setHover(null)}
                className={cn(
                  'flex items-center gap-2 rounded-full border px-3 py-1 text-xs transition',
                  off ? 'border-line text-neutral line-through' : 'border-line-control text-ink hover:border-ink/40',
                )}
              >
                <span className="h-2 w-2 rounded-full" style={{ background: off ? pal.reference : COLOURS[s] }} aria-hidden />
                <span className="font-medium">{s}</span>
                {pct !== undefined && (
                  <span className={cn('num', pct > 0 ? 'text-up' : pct < 0 ? 'text-down-text' : 'text-neutral')}>
                    {pct > 0 ? '+' : ''}
                    {pct.toFixed(2)}%
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="h-[260px]" data-testid="performance-chart">
          {pending ? (
            <Skeleton className="h-full w-full rounded-xl" />
          ) : failed ? (
            <ErrorState error={failed.error} onRetry={() => queries.forEach((q) => void q.refetch())} compact />
          ) : (
            <ResponsiveContainer>
              <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="time"
                  {...axisProps}
                  minTickGap={48}
                  tickFormatter={(t: number) => formatInZone(t * 1000, 'UTC', label)}
                />
                <YAxis {...axisProps} width={48} tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`} domain={['auto', 'auto']} />
                <ReferenceLine y={0} stroke={pal.reference} />
                <Tooltip
                  {...tooltipProps}
                  labelFormatter={(t: number) => `${formatInZone(t * 1000, 'UTC', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })} UTC`}
                  formatter={(v: number, name: string) => [`${v > 0 ? '+' : ''}${v.toFixed(2)}%`, name]}
                />
                {SYMBOLS.filter((s) => !hidden.includes(s) && !missing.includes(s)).map((s) => (
                  <Line
                    key={s}
                    type="linear"
                    dataKey={s}
                    stroke={COLOURS[s]}
                    strokeWidth={hover === s ? 3 : 2}
                    strokeOpacity={hover && hover !== s ? 0.25 : 1}
                    dot={false}
                    activeDot={{ r: 4, strokeWidth: 0 }}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
