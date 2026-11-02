import { WelcomeSection } from '@/components/dashboard/WelcomeSection';
import { AccountSummary } from '@/components/dashboard/AccountSummary';
import { MarketCards } from '@/components/dashboard/MarketCards';
import { SessionPanel } from '@/components/dashboard/SessionPanel';
import { DataHealthPanel } from '@/components/dashboard/DataHealthPanel';
import { IntelligenceSummary } from '@/components/dashboard/IntelligenceSummary';
import { RecentAlerts } from '@/components/dashboard/RecentAlerts';

export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-5">
      <WelcomeSection />
      <AccountSummary />
      <MarketCards />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <SessionPanel />
        <DataHealthPanel />
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <IntelligenceSummary />
        <RecentAlerts />
      </div>
    </div>
  );
}
