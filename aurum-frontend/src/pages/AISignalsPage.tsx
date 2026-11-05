import { useMemo, useState } from 'react';
import { ShieldAlert, Sparkles } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card } from '@/components/common/Card';
import { Select } from '@/components/common/Select';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { EmptyState } from '@/components/common/EmptyState';
import { QueryState } from '@/components/common/QueryState';
import { Badge } from '@/components/common/Badge';
import { SignalCard } from '@/components/signals/SignalCard';
import { SignalDrawer } from '@/components/signals/SignalDrawer';
import { SIGNAL_STATUS } from '@/config/signals';
import { useSignals } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import { demoProfile } from '@/mocks/market';
import type { ReviewAction } from '@/types/decision';
import type { SignalStatus, TradeSignal } from '@/types/trading';

export default function AISignalsPage() {
  const q = useSignals();
  const [instrument, setInstrument] = useState('');
  const [direction, setDirection] = useState('');
  const [timeframe, setTimeframe] = useState('');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState<{ signal: TradeSignal; action: ReviewAction | null } | null>(null);

  const filtered = useMemo(
    () =>
      (q.data?.data ?? []).filter(
        (s) =>
          (!instrument || s.instrument === instrument) &&
          (!direction || s.direction === direction) &&
          (!timeframe || s.timeframe === timeframe) &&
          (!status || s.status === status),
      ),
    [q.data, instrument, direction, timeframe, status],
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Trading"
        title="AI Signals"
        description="Trade candidates proposed by the strategy engine for human review. AURUM AI never executes trades automatically."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <div className="flex gap-2 rounded-card border border-warn/30 bg-warn-faint px-4 py-3 text-xs text-ink-muted">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <p>
          A confidence score reflects the model's internal agreement across evidence. It is <strong className="text-ink">not</strong> a guaranteed probability of
          success. Every candidate requires human approval and backend risk checks. Approving records your decision. AURUM AI does not place trades.
        </p>
      </div>
      <Card className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-4">
        <Select label="Instrument" value={instrument} onChange={setInstrument} options={[{ value: '', label: 'All' }, ...DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))]} />
        <Select label="Direction" value={direction} onChange={setDirection} options={[{ value: '', label: 'All' }, { value: 'long', label: 'Long' }, { value: 'short', label: 'Short' }]} />
        <Select label="Timeframe" value={timeframe} onChange={setTimeframe} options={[{ value: '', label: 'All' }, ...TIMEFRAMES.map((t) => ({ value: t, label: t }))]} />
        <Select
          label="Status"
          value={status}
          onChange={setStatus}
          options={[{ value: '', label: 'All' }, ...(Object.keys(SIGNAL_STATUS) as SignalStatus[]).map((s) => ({ value: s, label: SIGNAL_STATUS[s].label }))]}
        />
      </Card>
      <QueryState
        query={q}
        isEmpty={() => filtered.length === 0}
        empty={
          <Card>
            <EmptyState
              icon={<Sparkles className="h-5 w-5" aria-hidden />}
              title={(q.data?.data.length ?? 0) === 0 ? 'No trade candidates yet' : 'No signals match your filters'}
              description={
                (q.data?.data.length ?? 0) === 0 ? (
                  <>
                    Signals will appear here once the Python strategy engine publishes candidates to <span className="num">GET /analysis/signals</span>. AURUM AI
                    does not generate placeholder signals.
                  </>
                ) : undefined
              }
              action={
                <div className="flex flex-wrap justify-center gap-1.5">
                  {(Object.keys(SIGNAL_STATUS) as SignalStatus[]).map((s) => (
                    <Badge key={s} tone={SIGNAL_STATUS[s].tone}>
                      {SIGNAL_STATUS[s].label}
                    </Badge>
                  ))}
                </div>
              }
            />
          </Card>
        }
      >
        {() => (
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {filtered.map((s) => (
              <SignalCard key={s.id} s={s} digits={demoProfile(s.instrument).digits} onOpen={(action) => setOpen({ signal: s, action })} />
            ))}
          </div>
        )}
      </QueryState>
      <SignalDrawer id={open?.signal.id ?? null} digits={open ? demoProfile(open.signal.instrument).digits : undefined} action={open?.action} onClose={() => setOpen(null)} />
    </div>
  );
}
