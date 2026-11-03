import { useMemo } from 'react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartCard } from '@/components/analytics/ChartCard';
import { axisProps, gridProps, legendProps, SERIES, tooltipProps } from '@/components/analytics/chartStyles';
import { formatCompact } from '@/utils/format';
import type { CotHistory } from '@/types/cot';
import { CATEGORY_COLORS } from './cotFormat';

/**
 * D/E. COT charts for ONE report family. Categories from different families (e.g. Legacy "commercial" vs
 * Disaggregated "producer/merchant") are never mixed in one chart.
 */
type Series = CotHistory['series'];
interface Props {
  series: Series;
  categories: CotHistory['categories'];
  primary: string;
}

const xTick = (d: string) => d.slice(2, 7);
const yTick = (v: number) => formatCompact(v);
const num = (v: number) => v.toLocaleString();
const labelOf = (cats: Props['categories'], key: string) => cats.find((c) => c.key === key)?.label ?? key;

/** Flatten series -> one row per report with `${category}_${field}` keys for Recharts. */
function useRows(series: Series) {
  return useMemo(
    () =>
      series.map((s) => {
        const row: Record<string, number | string | null> = { reportDate: s.reportDate, releaseDate: s.releaseDate, openInterest: s.openInterest };
        for (const c of s.categories) {
          row[`${c.category}_net`] = c.net;
          row[`${c.category}_long`] = c.long;
          row[`${c.category}_short`] = c.short;
          row[`${c.category}_chg`] = c.weeklyNetChange ?? null;
          row[`${c.category}_p12`] = c.historicalPercentile12m ?? null;
          row[`${c.category}_p36`] = c.historicalPercentile36m ?? null;
          row[`${c.category}_idx`] = c.cotIndex ?? null;
        }
        return row;
      }),
    [series],
  );
}

const tooltipLabel = (label: string, payload: readonly { payload?: Record<string, unknown> }[] | undefined) => {
  const rel = payload?.[0]?.payload?.releaseDate;
  return `Report ${label}${rel ? ` · released ${String(rel)}` : ''}`;
};

export function CategoryBreakdownChart({ series, categories }: Props) {
  const last = series.at(-1);
  const data = (last?.categories ?? [])
    .filter((c) => c.category !== 'total_reportable')
    .map((c) => ({ name: labelOf(categories, c.category), Long: c.long, Short: c.short, Spreading: c.spreading }));
  return (
    <ChartCard title="Trader category breakdown" subtitle={last ? `Long / short / spreading contracts as of ${last.reportDate}` : undefined} empty={!data.length}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="name" {...axisProps} interval={0} tick={{ ...axisProps.tick, fontSize: 11 }} />
          <YAxis {...axisProps} tickFormatter={yTick} width={52} />
          <Tooltip {...tooltipProps} formatter={num} />
          <Legend {...legendProps} />
          <Bar dataKey="Long" fill={SERIES.up} fillOpacity={0.75} />
          <Bar dataKey="Short" fill={SERIES.down} fillOpacity={0.75} />
          <Bar dataKey="Spreading" fill={SERIES.oi} fillOpacity={0.6} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function NetHistoryChart({ series, categories }: Props) {
  const rows = useRows(series);
  const cats = categories.filter((c) => c.key !== 'total_reportable');
  return (
    <ChartCard title="Net positioning history" subtitle="Net contracts (long − short) by trader category" empty={!rows.length}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={56} />
          <Tooltip {...tooltipProps} formatter={num} labelFormatter={tooltipLabel} />
          <Legend {...legendProps} />
          <ReferenceLine y={0} stroke="#3a4c69" />
          {cats.map((c) => (
            <Line key={c.key} type="monotone" dataKey={`${c.key}_net`} name={c.label} stroke={CATEGORY_COLORS[c.key] ?? '#8B98AA'} dot={false} strokeWidth={1.4} connectNulls={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function LongShortChart({ series, categories, primary }: Props) {
  const rows = useRows(series);
  return (
    <ChartCard title={`${labelOf(categories, primary)} long vs short`} subtitle="Gross long and short contracts" empty={!rows.length}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={56} />
          <Tooltip {...tooltipProps} formatter={num} labelFormatter={tooltipLabel} />
          <Legend {...legendProps} />
          <Line type="monotone" dataKey={`${primary}_long`} name="Long" stroke={SERIES.up} dot={false} strokeWidth={1.4} />
          <Line type="monotone" dataKey={`${primary}_short`} name="Short" stroke={SERIES.down} dot={false} strokeWidth={1.4} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function WeeklyChangeChart({ series, categories, primary }: Props) {
  const rows = useRows(series);
  const key = `${primary}_chg`;
  const has = rows.some((r) => r[key] != null);
  return (
    <ChartCard title="Weekly net change" subtitle={`${labelOf(categories, primary)}: change vs previous report`} empty={!has} emptyDescription="Calculated by the backend metric engine.">
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={56} />
          <Tooltip {...tooltipProps} formatter={num} labelFormatter={tooltipLabel} />
          <ReferenceLine y={0} stroke="#3a4c69" />
          <Bar dataKey={key} name="Weekly change">
            {rows.map((r) => (
              <Cell key={String(r.reportDate)} fill={Number(r[key] ?? 0) >= 0 ? SERIES.up : SERIES.down} fillOpacity={0.75} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function OpenInterestChart({ series }: Pick<Props, 'series'>) {
  const rows = useRows(series);
  return (
    <ChartCard title="Open interest" empty={!rows.length}>
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={56} domain={['auto', 'auto']} />
          <Tooltip {...tooltipProps} formatter={num} labelFormatter={tooltipLabel} />
          <Area type="monotone" dataKey="openInterest" name="Open interest" stroke={SERIES.oi} fill={SERIES.oi} fillOpacity={0.08} strokeWidth={1.5} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PercentileChart({ series, categories, primary }: Props) {
  const rows = useRows(series);
  const has = rows.some((r) => r[`${primary}_p36`] != null || r[`${primary}_p12`] != null);
  return (
    <ChartCard
      title="Historical percentile"
      subtitle={`${labelOf(categories, primary)} net vs its trailing 12- and 36-month distribution (0-100)`}
      empty={!has}
      emptyDescription="Needs enough history (at least 80 % of the lookback window)."
    >
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} domain={[0, 100]} ticks={[0, 10, 50, 90, 100]} width={36} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toFixed(1)} labelFormatter={tooltipLabel} />
          <Legend {...legendProps} />
          <ReferenceLine y={90} stroke={SERIES.down} strokeDasharray="4 4" />
          <ReferenceLine y={10} stroke={SERIES.up} strokeDasharray="4 4" />
          <Line type="monotone" dataKey={`${primary}_p12`} name="12M" stroke={SERIES.oi} dot={false} strokeWidth={1.2} />
          <Line type="monotone" dataKey={`${primary}_p36`} name="36M" stroke={SERIES.nonCommercial} dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function CotIndexChart({ series, categories, primary }: Props) {
  const rows = useRows(series);
  const key = `${primary}_idx`;
  return (
    <ChartCard title="COT index (3-year range)" subtitle={`${labelOf(categories, primary)}: 0 = 3Y low, 100 = 3Y high`} empty={!rows.some((r) => r[key] != null)} emptyDescription="Calculated by the backend metric engine.">
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} width={36} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toFixed(1)} labelFormatter={tooltipLabel} />
          <ReferenceLine y={50} stroke="#3a4c69" />
          <Area type="monotone" dataKey={key} name="COT index" stroke={SERIES.nonCommercial} fill={SERIES.nonCommercial} fillOpacity={0.1} strokeWidth={1.5} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
