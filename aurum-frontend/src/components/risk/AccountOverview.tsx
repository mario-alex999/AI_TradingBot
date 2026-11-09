import { AlertTriangle, ShieldAlert, ShieldCheck, ShieldOff } from 'lucide-react';
import type { UseQueryResult } from '@tanstack/react-query';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { MetricCard } from '@/components/common/MetricCard';
import { Badge } from '@/components/common/Badge';
import { EmptyState } from '@/components/common/EmptyState';
import { UsageBar } from './UsageBar';
import type { Sourced } from '@/types/api';
import type { RiskAccount, RiskStatus } from '@/types/risk';
import { describeError } from '@/utils/errors';
import { formatCurrency, formatDateTime, formatInteger, formatPercent } from '@/utils/format';
import { STATUS_LABEL, STATUS_TONE } from '@/utils/riskLabels';
import { cn } from '@/utils/cn';

const BANNER: Record<RiskStatus, { icon: JSX.Element; className: string; text: string }> = {
  WITHIN_LIMITS: { icon: <ShieldCheck className="h-5 w-5" />, className: 'border-up/30 bg-up-faint text-up', text: 'Every account limit has room. New trades are still assessed individually.' },
  WARNING: { icon: <AlertTriangle className="h-5 w-5" />, className: 'border-warn/30 bg-warn-faint text-warn', text: 'At least one limit is close. Assessments may return REVIEW.' },
  BLOCKED: { icon: <ShieldAlert className="h-5 w-5" />, className: 'border-down/30 bg-down-faint text-down-text', text: 'A limit is reached or a lock is active. New trades are blocked.' },
  DATA_UNAVAILABLE: { icon: <ShieldOff className="h-5 w-5" />, className: 'border-line-control bg-base-raised text-ink-muted', text: 'Account data is missing or stale. Assessments return ERROR, never PASS.' },
};

const PERIODS = [
  ['day', 'Daily loss'],
  ['week', 'Weekly loss'],
  ['month', 'Monthly loss'],
] as const;

/** Section A: account overview and overall status. Every figure comes from GET /risk/account. */
export function AccountOverview({ query }: { query: UseQueryResult<Sourced<RiskAccount | null>> }) {
  const a = query.data?.data ?? null;
  if (query.isError) {
    const e = describeError(query.error);
    return <EmptyState title={e.title} description={e.description} icon={<ShieldOff className="h-5 w-5" />} />;
  }
  if (query.isLoading) return <div className="h-24 animate-pulse rounded-card bg-base-raised" aria-label="Loading account" />;
  if (!a) return <EmptyState title="Risk engine not connected" description="Risk figures come only from the backend. Demo mode shows none." icon={<ShieldOff className="h-5 w-5" />} />;

  const banner = BANNER[a.overallStatus];
  const ccy = a.currency ?? 'USD';
  const money = (v: number | null | undefined) => formatCurrency(v, ccy);
  const tone = a.dataStatus === 'ok' ? 'flat' : 'muted';

  return (
    <section aria-label="Account overview" className="flex flex-col gap-4">
      <div role="status" className={cn('flex items-start gap-3 rounded-card border p-4', banner.className)}>
        {banner.icon}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {STATUS_LABEL[a.overallStatus]}
            {a.activeLocks > 0 && <span className="font-normal"> · {a.activeLocks} active lock{a.activeLocks > 1 ? 's' : ''}</span>}
          </p>
          <p className="mt-0.5 text-xs">{a.reason ?? banner.text}</p>
        </div>
        <div className="hidden flex-none flex-col items-end gap-1 text-2xs sm:flex">
          <Badge tone="neutral">Profile: {a.profile}</Badge>
          <span>Config v{a.configVersion}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Balance" value={money(a.balance)} tone={tone} hint={a.tradeMode ?? undefined} />
        <MetricCard label="Equity" value={money(a.equity)} tone={tone} hint={a.capturedAt ? `As of ${formatDateTime(a.capturedAt)}` : 'No snapshot'} />
        <MetricCard
          label="Floating P/L"
          value={money(a.floatingPnl)}
          tone={a.floatingPnl == null ? 'muted' : a.floatingPnl < 0 ? 'down' : a.floatingPnl > 0 ? 'up' : 'flat'}
        />
        <MetricCard label="Free margin" value={money(a.freeMargin)} tone={tone} hint={a.leverage ? `Leverage 1:${a.leverage}` : undefined} />
        <MetricCard
          label="Drawdown"
          value={formatPercent(a.drawdownPct)}
          tone={tone}
          hint={`Limit ${formatPercent(a.maxDrawdownPct)}`}
          info="Fall in equity from its highest recorded value. Reaching the limit locks new trades until an administrator releases them."
        />
        <MetricCard
          label="Losing streak"
          value={`${formatInteger(a.consecutiveLosses)} / ${formatInteger(a.maxConsecutiveLosses)}`}
          tone={a.consecutiveLosses >= a.maxConsecutiveLosses ? 'down' : 'flat'}
          info="Consecutive losing closed trades. Breakeven trades neither count nor reset the streak."
        />
      </div>

      <Card>
        <CardHeader
          title="Loss limits"
          subtitle={`Measured on ${a.lossBasis}, trading day rolls at 17:00 New York. Deposits and withdrawals are excluded.`}
          actions={<Badge tone={STATUS_TONE[a.overallStatus]}>{STATUS_LABEL[a.overallStatus]}</Badge>}
        />
        <CardBody className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          {PERIODS.map(([k, label]) => {
            const p = a.periods[k];
            return (
              <UsageBar
                key={k}
                label={`${label}${p ? ` (${p.label})` : ''}`}
                value={p?.lossPct ?? null}
                limit={p?.limitPct ?? null}
                format={(v) => formatPercent(v)}
                note={p?.startUncertain ? 'Period start uncertain: no snapshot near the boundary.' : p ? `Start ${money(p.startValue)}` : 'Not available'}
              />
            );
          })}
          <UsageBar label="Trades today" value={a.tradesToday} limit={a.maxTradesPerDay} format={(v) => formatInteger(v)} />
        </CardBody>
      </Card>
    </section>
  );
}
