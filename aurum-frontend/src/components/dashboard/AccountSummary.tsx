import { KpiStrip } from '@/components/common/KpiStrip';
import { useAccountSummary } from '@/hooks/queries';
import { formatCurrency, formatInteger, formatPercent } from '@/utils/format';

/** Account figures come only from GET /account/summary — never mocked. */
export function AccountSummary() {
  const q = useAccountSummary();
  const a = q.data?.data ?? null;
  const hint = q.isPending ? 'Loading' : a ? `Updated ${new Date(a.updatedAt).toISOString().slice(11, 19)} UTC` : 'Awaiting connection';
  const cur = a?.currency ?? 'USD';
  return (
    <KpiStrip
      variant="feature"
      title="Account summary"
      actions={
        <span className="rounded-full bg-feature-tag px-2.5 py-0.5 text-xs font-semibold text-gold">
          {a ? (a.environment === 'live' ? 'Live' : 'Demo account') : 'Awaiting MT5'}
        </span>
      }
      label="Account summary"
      items={[
        { label: 'Account balance', value: formatCurrency(a?.balance, cur), hint },
        { label: 'Equity', value: formatCurrency(a?.equity, cur), hint },
        { label: 'Free margin', value: formatCurrency(a?.freeMargin, cur), hint },
        { label: 'Margin level', value: formatPercent(a?.marginLevel), hint },
        { label: 'Open positions', value: formatInteger(a?.openPositions), hint },
        { label: 'Pending approvals', value: formatInteger(a?.pendingApprovals), hint },
      ]}
    />
  );
}
