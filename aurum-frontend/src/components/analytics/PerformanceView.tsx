import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { MetricCard } from '@/components/common/MetricCard';
import { Badge } from '@/components/common/Badge';
import { formatCurrency, formatInteger, formatNumber, formatPercent } from '@/utils/format';
import type { PerformanceReport } from '@/types/trading';
import { ChartCard } from './ChartCard';
import { GLOSSARY } from '@/config/glossary';
import { axisProps, gridProps, SERIES, tooltipProps } from './chartStyles';

interface Props {
  report: PerformanceReport | null;
  kind: 'live' | 'backtest';
  emptyDescription: string;
}

function drawdownSeries(curve: PerformanceReport['equityCurve']) {
  let peak = -Infinity;
  return curve.map((p) => {
    peak = Math.max(peak, p.equity);
    return { time: p.time, drawdown: peak > 0 ? ((p.equity - peak) / peak) * 100 : 0 };
  });
}

function PnlBars({ title, data }: { title: string; data: { name: string; pnl: number }[] }) {
  return (
    <ChartCard title={title} empty={data.length === 0} height={220}>
      <ResponsiveContainer>
        <BarChart data={data}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="name" {...axisProps} />
          <YAxis {...axisProps} width={56} />
          <Tooltip {...tooltipProps} formatter={(v: number) => formatCurrency(v)} />
          <Bar dataKey="pnl">
            {data.map((d) => (
              <Cell key={d.name} fill={d.pnl >= 0 ? SERIES.up : SERIES.down} fillOpacity={0.75} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Shared results layout for backtests and live performance. Never renders fabricated numbers. */
export function PerformanceView({ report, kind, emptyDescription }: Props) {
  const m = report?.metrics;
  const tone = m ? 'flat' : 'muted';
  const label = kind === 'backtest' ? <Badge tone="info">Backtest · simulated</Badge> : <Badge tone="up">Live trading</Badge>;
  const empty = !report;
  return (
    <div className="flex flex-col gap-5">
      <section aria-label="Performance metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <MetricCard label="Total trades" value={formatInteger(m?.totalTrades)} tone={tone} hint={label} />
        <MetricCard label="Winning / losing" value={m ? `${m.winningTrades} / ${m.losingTrades}` : '—'} tone={tone} />
        <MetricCard label="Win rate" value={formatPercent(m?.winRate)} tone={tone} />
        <MetricCard label="Profit factor" value={formatNumber(m?.profitFactor, 2)} tone={tone} info={GLOSSARY.profit_factor} />
        <MetricCard label="Expectancy" value={formatCurrency(m?.expectancy)} tone={tone} info={GLOSSARY.expectancy} />
        <MetricCard label="Average profit" value={formatCurrency(m?.averageProfit)} tone={m ? 'up' : 'muted'} />
        <MetricCard label="Average loss" value={formatCurrency(m?.averageLoss)} tone={m ? 'down' : 'muted'} />
        <MetricCard label="Max drawdown" value={formatPercent(m?.maxDrawdownPct)} tone={tone} />
        <MetricCard label="Sharpe ratio" value={formatNumber(m?.sharpe, 2)} tone={tone} hint="Risk-adjusted return" />
        <MetricCard label="Report" value={kind === 'backtest' ? 'Backtest' : 'Live'} tone="muted" hint={kind === 'backtest' ? 'Not live performance' : 'Realised results'} />
      </section>
      <div className="grid gap-5 xl:grid-cols-2">
        <ChartCard title="Equity curve" actions={label} empty={empty} emptyDescription={emptyDescription}>
          <ResponsiveContainer>
            <AreaChart data={report?.equityCurve ?? []}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="time" {...axisProps} minTickGap={32} />
              <YAxis {...axisProps} width={64} domain={['auto', 'auto']} />
              <Tooltip {...tooltipProps} formatter={(v: number) => formatCurrency(v)} />
              <Area dataKey="equity" stroke={SERIES.nonCommercial} fill={SERIES.nonCommercial} fillOpacity={0.08} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title="Drawdown" actions={label} empty={empty} emptyDescription={emptyDescription}>
          <ResponsiveContainer>
            <AreaChart data={drawdownSeries(report?.equityCurve ?? [])}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="time" {...axisProps} minTickGap={32} />
              <YAxis {...axisProps} width={48} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
              <Tooltip {...tooltipProps} formatter={(v: number) => `${v.toFixed(2)}%`} />
              <Area dataKey="drawdown" stroke={SERIES.down} fill={SERIES.down} fillOpacity={0.1} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title="Monthly returns" empty={empty} emptyDescription={emptyDescription}>
          <ResponsiveContainer>
            <BarChart data={report?.monthly ?? []}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="month" {...axisProps} />
              <YAxis {...axisProps} width={48} tickFormatter={(v: number) => `${v}%`} />
              <Tooltip {...tooltipProps} formatter={(v: number) => `${v.toFixed(2)}%`} />
              <Bar dataKey="returnPct">
                {(report?.monthly ?? []).map((d) => (
                  <Cell key={d.month} fill={d.returnPct >= 0 ? SERIES.up : SERIES.down} fillOpacity={0.75} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        <PnlBars title="Performance by instrument" data={report?.byInstrument ?? []} />
        <PnlBars title="Performance by session" data={report?.bySession ?? []} />
        <PnlBars title="Strategy comparison" data={report?.byStrategy ?? []} />
      </div>
    </div>
  );
}
