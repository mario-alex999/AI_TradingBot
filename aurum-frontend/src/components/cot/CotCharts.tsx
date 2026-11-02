import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartCard } from '@/components/analytics/ChartCard';
import { axisProps, gridProps, legendProps, SERIES, tooltipProps } from '@/components/analytics/chartStyles';
import { formatCompact } from '@/utils/format';
import type { CotDerived } from '@/utils/cot';

const xTick = (d: string) => d.slice(2, 7);
const yTick = (v: number) => formatCompact(v);

export function NetPositioningChart({ rows }: { rows: CotDerived[] }) {
  return (
    <ChartCard title="Historical net positioning" subtitle="Non-commercial (large speculator) net contracts" empty={rows.length === 0}>
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="ncFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={SERIES.nonCommercial} stopOpacity={0.25} />
              <stop offset="100%" stopColor={SERIES.nonCommercial} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={52} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toLocaleString()} />
          <ReferenceLine y={0} stroke="#3a4c69" />
          <Area type="monotone" dataKey="nonCommercialNet" name="Non-commercial net" stroke={SERIES.nonCommercial} fill="url(#ncFill)" strokeWidth={1.5} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function GroupComparisonChart({ rows, title = 'Commercial vs non-commercial positioning' }: { rows: CotDerived[]; title?: string }) {
  return (
    <ChartCard title={title} subtitle="Net contracts by trader category" empty={rows.length === 0}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={52} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toLocaleString()} />
          <Legend {...legendProps} />
          <ReferenceLine y={0} stroke="#3a4c69" />
          <Line type="monotone" dataKey="commercialNet" name="Commercial" stroke={SERIES.commercial} dot={false} strokeWidth={1.5} />
          <Line type="monotone" dataKey="nonCommercialNet" name="Non-commercial" stroke={SERIES.nonCommercial} dot={false} strokeWidth={1.5} />
          <Line type="monotone" dataKey="nonReportableNet" name="Non-reportable" stroke={SERIES.nonReportable} dot={false} strokeWidth={1.2} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function OpenInterestChart({ rows }: { rows: CotDerived[] }) {
  return (
    <ChartCard title="Open interest trend" empty={rows.length === 0}>
      <ResponsiveContainer>
        <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={52} domain={['auto', 'auto']} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toLocaleString()} />
          <Area type="monotone" dataKey="openInterest" name="Open interest" stroke={SERIES.oi} fill={SERIES.oi} fillOpacity={0.08} strokeWidth={1.5} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PositioningChangeChart({ rows }: { rows: CotDerived[] }) {
  return (
    <ChartCard title="Weekly positioning change" subtitle="Change in non-commercial net contracts" empty={rows.length === 0}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} tickFormatter={yTick} width={52} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toLocaleString()} />
          <ReferenceLine y={0} stroke="#3a4c69" />
          <Bar dataKey="nonCommercialNetChange" name="Weekly change">
            {rows.map((r) => (
              <Cell key={r.reportDate} fill={(r.nonCommercialNetChange ?? 0) >= 0 ? SERIES.up : SERIES.down} fillOpacity={0.75} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PercentileChart({ rows }: { rows: CotDerived[] }) {
  const data = rows.filter((r) => r.nonCommercialPercentile != null);
  return (
    <ChartCard title="Positioning percentile" subtitle="Non-commercial net vs trailing 3-year range (0–100)" empty={data.length === 0} emptyDescription="Needs at least 26 weeks of history.">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="reportDate" {...axisProps} tickFormatter={xTick} minTickGap={28} />
          <YAxis {...axisProps} domain={[0, 100]} ticks={[0, 10, 50, 90, 100]} width={36} />
          <Tooltip {...tooltipProps} formatter={(v: number) => v.toFixed(1)} />
          <ReferenceLine y={90} stroke={SERIES.down} strokeDasharray="4 4" />
          <ReferenceLine y={10} stroke={SERIES.up} strokeDasharray="4 4" />
          <Line type="monotone" dataKey="nonCommercialPercentile" name="Percentile" stroke={SERIES.nonCommercial} dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
