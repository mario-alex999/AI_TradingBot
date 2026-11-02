import { Activity, CheckSquare, Gauge, Landmark, Scale, Wallet } from 'lucide-react';
import { MetricCard } from '@/components/common/MetricCard';
import { Skeleton } from '@/components/common/Skeleton';
import { useAccountSummary } from '@/hooks/queries';
import { formatCurrency, formatInteger, formatPercent } from '@/utils/format';

/** Account figures come only from GET /account/summary — never mocked. */
export function AccountSummary() {
  const q = useAccountSummary();
  const a = q.data?.data ?? null;
  const hint = q.isPending ? <Skeleton className="h-3 w-24" /> : a ? `Updated ${new Date(a.updatedAt).toISOString().slice(11, 19)} UTC` : 'Awaiting connection';
  const cur = a?.currency ?? 'USD';
  const cards = [
    { label: 'Account balance', value: formatCurrency(a?.balance, cur), icon: <Landmark className="h-4 w-4" /> },
    { label: 'Equity', value: formatCurrency(a?.equity, cur), icon: <Wallet className="h-4 w-4" /> },
    { label: 'Free margin', value: formatCurrency(a?.freeMargin, cur), icon: <Scale className="h-4 w-4" /> },
    { label: 'Margin level', value: formatPercent(a?.marginLevel), icon: <Gauge className="h-4 w-4" /> },
    { label: 'Open positions', value: formatInteger(a?.openPositions), icon: <Activity className="h-4 w-4" /> },
    { label: 'Pending approvals', value: formatInteger(a?.pendingApprovals), icon: <CheckSquare className="h-4 w-4" /> },
  ];
  return (
    <section aria-label="Account summary" className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
      {cards.map((c) => (
        <MetricCard key={c.label} label={c.label} value={c.value} icon={c.icon} tone={a ? 'flat' : 'muted'} hint={hint} />
      ))}
    </section>
  );
}
