import { useState } from 'react';
import { AdminJobButton } from '@/components/common/AdminJobButton';
import { Link } from 'react-router-dom';
import { PageHeader } from '@/components/common/PageHeader';
import { Select } from '@/components/common/Select';
import { Tabs } from '@/components/common/Tabs';
import { Badge } from '@/components/common/Badge';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { demoProfile } from '@/mocks/market';
import { useIntelligenceStatus } from '@/hooks/queries';
import { EconomicCalendarPanel, NewsPanel } from '@/components/intelligence/CalendarPanels';
import { MacroPanel, MarketConditionsPanel, MarketImpactPanel, VolatilityPanel } from '@/components/intelligence/MarketPanels';
import { SessionLiquidityPanel } from '@/components/intelligence/SessionPanels';

type Tab = 'overview' | 'calendar' | 'volatility' | 'macro' | 'liquidity' | 'news';
const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Impact & conditions' },
  { id: 'calendar', label: 'Economic calendar' },
  { id: 'volatility', label: 'Volatility' },
  { id: 'liquidity', label: 'Session liquidity' },
  { id: 'macro', label: 'Central banks & macro' },
  { id: 'news', label: 'Announcements' },
];

/** Module 04: market context for decision support. Read-only; never a trade signal. */
export default function MarketIntelligencePage() {
  const [symbol, setSymbol] = useState('EURUSD');
  const [tab, setTab] = useState<Tab>('overview');
  const status = useIntelligenceStatus();
  const st = status.data?.data;
  const digits = demoProfile(symbol).digits; // display precision only
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="Market Intelligence"
        description={
          <>
            Sessions, scheduled events, central-bank policy, volatility and market conditions for the selected instrument. This is context for a human
            decision: it does not place trades or predict direction. Session timing is on{' '}
            <Link to="/sessions" className="text-ink underline decoration-line-control underline-offset-4 hover:decoration-ink">
              Trading Sessions
            </Link>
            .
          </>
        }
        actions={
          <>
            {st && (
              <Badge tone={st.calendarCoverage?.complete ? 'up' : 'warn'}>
                Calendar coverage: {st.calendarCoverage?.level ?? 'unknown'}
              </Badge>
            )}
            {st && !st.schemaReady && <Badge tone="down">Module 04 tables missing</Badge>}
            <AdminJobButton action="calendarRefresh" invalidate={['mi-calendar', 'mi-news', 'mi-macro', 'mi-market', 'mi-risk', 'mi-status', 'news-impact']} />
            <AdminJobButton action="newsRefresh" invalidate={['mi-calendar', 'mi-news', 'mi-macro', 'mi-market', 'mi-risk', 'mi-status', 'news-impact']} />
            <AdminJobButton action="macroRefresh" invalidate={['mi-calendar', 'mi-news', 'mi-macro', 'mi-market', 'mi-risk', 'mi-status', 'news-impact']} />
            <Select label="Instrument" hideLabel value={symbol} onChange={setSymbol} options={DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))} className="w-32" />
          </>
        }
      />
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Market intelligence sections" />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="flex flex-col gap-5">
        {tab === 'overview' && (
          <div className="grid gap-5 xl:grid-cols-2">
            <MarketImpactPanel symbol={symbol} />
            <MarketConditionsPanel symbol={symbol} />
          </div>
        )}
        {tab === 'calendar' && <EconomicCalendarPanel />}
        {tab === 'volatility' && <VolatilityPanel symbol={symbol} digits={digits} />}
        {tab === 'liquidity' && <SessionLiquidityPanel symbol={symbol} digits={digits} />}
        {tab === 'macro' && <MacroPanel />}
        {tab === 'news' && <NewsPanel />}
      </div>
    </div>
  );
}
