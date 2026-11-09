import { useState } from 'react';
import { PageHeader } from '@/components/common/PageHeader';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Badge } from '@/components/common/Badge';
import { Tabs } from '@/components/common/Tabs';
import { AccountOverview } from '@/components/risk/AccountOverview';
import { LocksPanel } from '@/components/risk/LocksPanel';
import { RiskCalculator } from '@/components/risk/RiskCalculator';
import { PositionSizeCalculator } from '@/components/risk/PositionSizeCalculator';
import { ExposurePanel } from '@/components/risk/ExposurePanel';
import { RiskSettings } from '@/components/risk/RiskSettings';
import { AssessmentHistory } from '@/components/risk/AssessmentHistory';
import { useRiskAccount, useRiskHealth } from '@/hooks/useRisk';

type Tab = 'calculator' | 'exposure' | 'settings' | 'history';
const TABS: { id: Tab; label: string }[] = [
  { id: 'calculator', label: 'Calculators' },
  { id: 'exposure', label: 'Exposure' },
  { id: 'settings', label: 'Settings' },
  { id: 'history', label: 'History' },
];

/**
 * Module 06 dashboard. Every figure and decision comes from the backend risk engine (/api/v1/risk); the only
 * browser-side maths is the original informational calculator, which is labelled as such. Nothing here places orders.
 */
export default function RiskManagementPage() {
  const account = useRiskAccount();
  const health = useRiskHealth();
  const [tab, setTab] = useState<Tab>('calculator');
  const h = health.data?.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Trading"
        title="Risk Management"
        description="Account limits, locks, position sizing and exposure, decided by the backend risk engine. AURUM AI never places orders."
        actions={
          <div className="flex items-center gap-2">
            {h && <Badge tone={h.status === 'ok' ? 'up' : 'warn'}>Engine {h.engineVersion} · {h.status}</Badge>}
            <DataSourceBadge source={account.data?.source} fetchedAt={account.data?.fetchedAt} />
          </div>
        }
      />
      <AccountOverview query={account} />
      <LocksPanel />
      <Tabs label="Risk sections" tabs={TABS} value={tab} onChange={setTab} />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'calculator' && (
          <div className="flex flex-col gap-5">
            <PositionSizeCalculator />
            <RiskCalculator />
          </div>
        )}
        {tab === 'exposure' && <ExposurePanel />}
        {tab === 'settings' && <RiskSettings />}
        {tab === 'history' && <AssessmentHistory />}
      </div>
    </div>
  );
}
