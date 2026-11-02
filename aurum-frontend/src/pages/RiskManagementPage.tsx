import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ShieldCheck } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { MetricCard } from '@/components/common/MetricCard';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Badge } from '@/components/common/Badge';
import { describeError } from '@/utils/errors';
import { ChartCard } from '@/components/analytics/ChartCard';
import { axisProps, gridProps, tooltipProps } from '@/components/analytics/chartStyles';
import { PositionSizeCalculator } from '@/components/risk/PositionSizeCalculator';
import { useRiskOverview } from '@/hooks/queries';
import { formatCurrency, formatInteger, formatPercent } from '@/utils/format';

function ExposureChart({ title, data }: { title: string; data: { name: string; value: number }[] }) {
  return (
    <ChartCard title={title} empty={data.length === 0} emptyTitle="No exposure data" emptyDescription="Exposure is reported by the backend Risk Management Agent.">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid {...gridProps} horizontal={false} vertical />
          <XAxis type="number" {...axisProps} tickFormatter={(v: number) => `${v}%`} />
          <YAxis type="category" dataKey="name" {...axisProps} width={72} />
          <Tooltip {...tooltipProps} formatter={(v: number) => `${v.toFixed(2)}%`} />
          <Bar dataKey="value" fill="#D6AD60" radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export default function RiskManagementPage() {
  const q = useRiskOverview();
  const r = q.data?.data ?? null;
  const tone = r ? 'flat' : 'muted';
  const hint = q.isError ? describeError(q.error).title : r ? undefined : 'Awaiting connection';
  const limits = r?.limits;
  const limitRows: [string, string][] = [
    ['Maximum risk per trade', formatPercent(limits?.maxRiskPerTradePct)],
    ['Maximum daily loss', formatPercent(limits?.maxDailyLossPct)],
    ['Maximum open positions', formatInteger(limits?.maxOpenPositions)],
    ['Maximum total exposure', formatPercent(limits?.maxTotalExposurePct)],
    ['Maximum correlated exposure', formatPercent(limits?.maxCorrelatedExposurePct)],
  ];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Trading"
        title="Risk Management"
        description="Account risk, drawdown and exposure as reported and enforced by the backend."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <section aria-label="Risk metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Account risk" value={formatPercent(r?.accountRiskPct)} tone={tone} hint={hint} />
        <MetricCard label="Risk per trade" value={formatPercent(r?.riskPerTradePct)} tone={tone} hint={hint} />
        <MetricCard label="Daily drawdown" value={formatPercent(r?.dailyDrawdownPct)} tone={tone} hint={hint} />
        <MetricCard label="Weekly drawdown" value={formatPercent(r?.weeklyDrawdownPct)} tone={tone} hint={hint} />
        <MetricCard label="Max drawdown" value={formatPercent(r?.maxDrawdownPct)} tone={tone} hint={hint} />
        <MetricCard label="Open risk" value={formatCurrency(r?.openRisk)} tone={tone} hint={hint} />
      </section>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)]">
        <ExposureChart title="Exposure by instrument" data={r?.exposureByInstrument ?? []} />
        <ExposureChart title="Exposure by asset class" data={r?.exposureByAssetClass ?? []} />
        <Card>
          <CardHeader
            icon={<ShieldCheck className="h-4 w-4" />}
            title="Risk limits"
            subtitle="Configured and enforced server-side. Displayed read-only."
            actions={<Badge tone={limits ? 'up' : 'neutral'}>{limits ? 'Backend enforced' : 'Not reported'}</Badge>}
          />
          <CardBody>
            <dl>
              {limitRows.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3 border-b border-line-soft py-2 text-xs last:border-0">
                  <dt className="text-neutral">{k}</dt>
                  <dd className="num text-ink">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-2xs text-neutral">Limits are changed through the backend's administrative API, never from this browser.</p>
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Risk / reward analysis" subtitle="Distribution of planned vs realised R-multiples." />
        <CardBody className="text-xs text-neutral">Available once closed trades with recorded stop distances are reported by the backend.</CardBody>
      </Card>
      <PositionSizeCalculator />
    </div>
  );
}
