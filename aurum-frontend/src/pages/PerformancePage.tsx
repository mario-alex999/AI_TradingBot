import { useState } from 'react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card } from '@/components/common/Card';
import { Select } from '@/components/common/Select';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { PerformanceView } from '@/components/analytics/PerformanceView';
import { usePerformance } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';

export default function PerformancePage() {
  const [f, setF] = useState({ from: '', to: '', strategy: '', instrument: '', timeframe: '', session: '' });
  const q = usePerformance('live', f);
  const set = (k: keyof typeof f) => (v: string) => setF((s) => ({ ...s, [k]: v }));
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Analytics"
        title="Performance Analytics"
        description="Realised strategy performance from closed trades. Backtests are reported separately."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <Card className="grid gap-3 p-3 sm:grid-cols-3 xl:grid-cols-6">
        <div className="flex flex-col gap-1">
          <label htmlFor="pf-from" className="text-xs font-medium text-ink-muted">From</label>
          <input id="pf-from" type="date" className="field num" value={f.from} onChange={(e) => set('from')(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="pf-to" className="text-xs font-medium text-ink-muted">To</label>
          <input id="pf-to" type="date" className="field num" value={f.to} onChange={(e) => set('to')(e.target.value)} />
        </div>
        <Select label="Strategy" value={f.strategy} onChange={set('strategy')} options={[{ value: '', label: 'All strategies' }]} />
        <Select label="Instrument" value={f.instrument} onChange={set('instrument')} options={[{ value: '', label: 'All' }, ...DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))]} />
        <Select label="Timeframe" value={f.timeframe} onChange={set('timeframe')} options={[{ value: '', label: 'All' }, ...TIMEFRAMES.map((t) => ({ value: t, label: t }))]} />
        <Select
          label="Session"
          value={f.session}
          onChange={set('session')}
          options={[
            { value: '', label: 'All sessions' },
            { value: 'asia', label: 'Asian' },
            { value: 'london', label: 'London' },
            { value: 'newyork', label: 'New York' },
          ]}
        />
      </Card>
      <PerformanceView report={q.data?.data ?? null} kind="live" emptyDescription="No closed trades have been reported. Performance is never simulated." />
    </div>
  );
}
