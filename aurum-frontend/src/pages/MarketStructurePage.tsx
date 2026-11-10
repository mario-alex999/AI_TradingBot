import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { useAdminGate } from '@/hooks/useDecision';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { toast } from '@/stores/toastStore';
import { describeError } from '@/utils/errors';
import { runStructureAnalysis } from '@/services/adminJobService';
import { ChevronRight } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { PageHeader } from '@/components/common/PageHeader';
import { Badge, type Tone } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { InfoTip } from '@/components/common/InfoTip';
import { Drawer } from '@/components/common/Drawer';
import { useInstruments, useMarketStructure } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import { GLOSSARY } from '@/config/glossary';
import { formatDateTime, formatNumber } from '@/utils/format';
import { cn } from '@/utils/cn';
import type { Bias, StructureEvent, TimeframeStructure } from '@/types/analysis';
import type { Timeframe } from '@/types/market';

const BIAS: Record<Bias, { label: string; tone: Tone }> = {
  bullish: { label: 'Bullish', tone: 'up' },
  bearish: { label: 'Bearish', tone: 'down' },
  ranging: { label: 'Ranging', tone: 'info' },
  awaiting: { label: 'Awaiting confirmation', tone: 'neutral' },
};

const EVENT_ROWS: { type: StructureEvent['type']; label: string }[] = [
  { type: 'HH', label: 'Higher highs' },
  { type: 'HL', label: 'Higher lows' },
  { type: 'LH', label: 'Lower highs' },
  { type: 'LL', label: 'Lower lows' },
  { type: 'BOS', label: 'Break of structure' },
  { type: 'CHOCH', label: 'Change of character' },
  { type: 'displacement', label: 'Displacement' },
  { type: 'liquidity_sweep', label: 'Liquidity sweeps' },
  { type: 'fvg', label: 'Fair value gaps' },
  { type: 'order_block', label: 'Order blocks' },
];

const LABEL = Object.fromEntries(EVENT_ROWS.map((r) => [r.type, r.label])) as Record<StructureEvent['type'], string>;

/** Premium / discount bar: where equilibrium sits in the dealing range. */
function RangeBar({ pd }: { pd: NonNullable<TimeframeStructure['premiumDiscount']> }) {
  const span = pd.rangeHigh - pd.rangeLow || 1;
  const eq = ((pd.equilibrium - pd.rangeLow) / span) * 100;
  return (
    <div>
      <div className="relative h-8 overflow-hidden rounded-lg">
        <div className="absolute inset-y-0 left-0 bg-up-faint" style={{ width: `${eq}%` }} title="Discount" />
        <div className="absolute inset-y-0 right-0 bg-down-faint" style={{ width: `${100 - eq}%` }} title="Premium" />
        <div className="absolute inset-y-0 w-0.5 bg-gold" style={{ left: `${eq}%` }} />
        <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs font-medium text-up">Discount</span>
        <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs font-medium text-down-text">Premium</span>
      </div>
      <div className="num mt-1 flex justify-between text-xs text-neutral">
        <span>Low {formatNumber(pd.rangeLow, 5)}</span>
        <span className="text-ink">Equilibrium {formatNumber(pd.equilibrium, 5)}</span>
        <span>High {formatNumber(pd.rangeHigh, 5)}</span>
      </div>
    </div>
  );
}

/** Module 03 admin action: analyse the instrument across timeframes now and store the result. */
function RunStructureButton({ symbol }: { symbol: string }) {
  const gate = useAdminGate();
  const key = useAdminKeyStore((s) => s.key);
  const qc = useQueryClient();
  const run = useMutation({
    mutationFn: () => runStructureAnalysis(symbol, key),
    onSuccess: (r) => {
      toast({ tone: 'success', title: `${r.symbol} analysed`, description: `Bias ${r.bias}, grade ${r.grade}.` });
      for (const k of ['structure', 'overlays']) void qc.invalidateQueries({ queryKey: [k] });
    },
    onError: (e) => toast({ tone: 'error', title: 'Analysis failed', description: describeError(e).description }),
  });
  return (
    <Button size="sm" variant="secondary" icon={<RefreshCw className={run.isPending ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} aria-hidden />}
      disabled={!gate.allowed || run.isPending} disabledReason={run.isPending ? 'Running…' : gate.reason} onClick={() => run.mutate()}
      title="Admin action: analyse every timeframe on stored candles and store the result">
      {run.isPending ? 'Analysing…' : `Analyse ${symbol}`}
    </Button>
  );
}

export default function MarketStructurePage() {
  const instruments = useInstruments();
  const [symbol, setSymbol] = useState('EURUSD');
  const [tf, setTf] = useState<Timeframe>('H4');
  const [cell, setCell] = useState<{ type: StructureEvent['type']; tf: Timeframe } | null>(null);
  const q = useMarketStructure(symbol);
  const report = q.data?.data ?? null;
  const byTf = useMemo(() => new Map<Timeframe, TimeframeStructure>((report?.timeframes ?? []).map((t) => [t.timeframe, t])), [report]);
  const listRef = useRef<HTMLDivElement>(null);
  const selected = byTf.get(tf);
  const cellEvents = cell ? (byTf.get(cell.tf)?.events ?? []).filter((e) => e.type === cell.type) : [];

  // Arrow keys move between timeframes (roving focus).
  const onListKey = (e: KeyboardEvent) => {
    const i = TIMEFRAMES.indexOf(tf);
    const next = e.key === 'ArrowDown' ? i + 1 : e.key === 'ArrowUp' ? i - 1 : null;
    if (next === null) return;
    e.preventDefault();
    const t = TIMEFRAMES[(next + TIMEFRAMES.length) % TIMEFRAMES.length]!;
    setTf(t);
    listRef.current?.querySelector<HTMLButtonElement>(`[data-tf="${t}"]`)?.focus();
  };

  const symbols = (instruments.data?.data ?? DEFAULT_INSTRUMENTS).map((i) => i.symbol);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Market Structure"
        description="Multi-timeframe swing structure, breaks and liquidity events from the Market Structure Agent. Select a timeframe, or click any cell to see the events behind it."
        actions={
          <>
            <RunStructureButton symbol={symbol} />
            <DataSourceBadge source={q.data?.source} />
          </>
        }
      />
      <SegmentedControl label="Instrument" value={symbol} options={symbols.map((s) => ({ value: s, label: s }))} onChange={setSymbol} />

      {q.isPending ? (
        <SkeletonRows rows={6} />
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
            <Card>
              <CardHeader title="Structure by timeframe" subtitle={report ? `Generated ${formatDateTime(report.generatedAt)} UTC` : `No analysis received for ${symbol} yet`} />
              <CardBody>
                <div ref={listRef} role="listbox" aria-label="Timeframes" aria-activedescendant={`tf-${tf}`} onKeyDown={onListKey} className="flex flex-col gap-1">
                  {TIMEFRAMES.map((t) => {
                    const b = BIAS[byTf.get(t)?.bias ?? 'awaiting'];
                    const active = t === tf;
                    return (
                      <button
                        key={t}
                        id={`tf-${t}`}
                        data-tf={t}
                        role="option"
                        aria-selected={active}
                        tabIndex={active ? 0 : -1}
                        onClick={() => setTf(t)}
                        className={cn(
                          'flex items-center justify-between rounded-lg px-3 py-2.5 text-left transition',
                          active ? 'bg-gold text-white' : 'hover:bg-base-raised',
                        )}
                      >
                        <span className={cn('num text-sm font-semibold', active ? 'text-white' : 'text-ink')}>{t}</span>
                        <span className="flex items-center gap-2">
                          {active ? <span className="text-xs text-white/85">{b.label}</span> : <Badge tone={b.tone}>{b.label}</Badge>}
                          <ChevronRight className={cn('h-4 w-4', active ? 'text-white' : 'text-neutral')} aria-hidden />
                        </span>
                      </button>
                    );
                  })}
                </div>
                <p className="mt-3 text-xs text-neutral">Tip: use ↑ ↓ to move between timeframes.</p>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Events by timeframe" subtitle="Counts in the current analysis window. Click a cell for the list." />
              <div className="overflow-x-auto pb-2">
                <table className="w-full min-w-[640px] text-sm">
                  <caption className="sr-only">Structure events by timeframe</caption>
                  <thead>
                    <tr className="border-b border-line-soft">
                      <th scope="col" className="px-4 py-2.5 text-left text-xs font-normal text-neutral">
                        Event
                      </th>
                      {TIMEFRAMES.map((t) => (
                        <th key={t} scope="col" className={cn('num px-3 py-2.5 text-right text-xs font-semibold', t === tf ? 'text-gold' : 'text-neutral')}>
                          <button type="button" onClick={() => setTf(t)} className="hover:underline">
                            {t}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {EVENT_ROWS.map((row) => (
                      <tr key={row.type} className="border-b border-line-soft last:border-0">
                        <th scope="row" className="px-4 py-1.5 text-left font-normal text-ink-muted">
                          <span className="flex items-center gap-1">
                            {row.label}
                            <InfoTip label={`What is ${row.label.toLowerCase()}?`}>{GLOSSARY[row.type]}</InfoTip>
                          </span>
                        </th>
                        {TIMEFRAMES.map((t) => {
                          const s = byTf.get(t);
                          const n = s ? s.events.filter((e) => e.type === row.type).length : null;
                          return (
                            <td key={t} className={cn('px-1 py-1 text-right', t === tf && 'bg-gold-faint')}>
                              <button
                                type="button"
                                onClick={() => setCell({ type: row.type, tf: t })}
                                aria-label={`${row.label} on ${t}: ${n ?? 'awaiting analysis'}`}
                                className="num w-full rounded-md px-2 py-1 text-right text-ink transition hover:bg-base-raised"
                              >
                                {n ?? '—'}
                              </button>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader
              title={`${symbol} · ${tf}`}
              subtitle="The selected timeframe in detail."
              actions={<Badge tone={BIAS[selected?.bias ?? 'awaiting'].tone}>{BIAS[selected?.bias ?? 'awaiting'].label}</Badge>}
            />
            <CardBody className="grid gap-5 lg:grid-cols-2">
              <div>
                <p className="mb-2 flex items-center gap-1 text-sm font-medium text-ink">
                  Premium and discount <InfoTip label="What are premium and discount?">{GLOSSARY.premium_discount}</InfoTip>
                </p>
                {selected?.premiumDiscount ? (
                  <RangeBar pd={selected.premiumDiscount} />
                ) : (
                  <p className="rounded-lg border border-dashed border-line-control px-3 py-4 text-sm text-neutral">No dealing range for {tf} yet.</p>
                )}
              </div>
              <div>
                <p className="mb-2 text-sm font-medium text-ink">Latest events</p>
                {selected && selected.events.length ? (
                  <ul className="flex flex-col">
                    {[...selected.events].sort((a, b) => b.time.localeCompare(a.time)).slice(0, 6).map((e, i) => (
                      <li key={i} className="flex items-center justify-between border-b border-line-soft py-2 text-sm last:border-0">
                        <span className="flex items-center gap-2">
                          <Badge tone={e.direction === 'bullish' ? 'up' : e.direction === 'bearish' ? 'down' : 'neutral'}>{e.type}</Badge>
                          <span className="text-ink-muted">{LABEL[e.type]}</span>
                        </span>
                        <span className="num text-xs text-neutral">
                          {formatNumber(e.price, 5)} · {formatDateTime(e.time)}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="rounded-lg border border-dashed border-line-control px-3 py-4 text-sm text-neutral">
                    {report ? `No events detected on ${tf} in the current window.` : 'Events appear once the Market Structure Agent publishes analysis.'}
                  </p>
                )}
              </div>
            </CardBody>
          </Card>
        </>
      )}

      <Drawer
        open={cell !== null}
        onClose={() => setCell(null)}
        title={cell ? `${LABEL[cell.type]} · ${symbol} ${cell.tf}` : ''}
        subtitle={cell ? GLOSSARY[cell.type] : undefined}
      >
        {cellEvents.length === 0 ? (
          <p className="text-sm text-neutral">
            {report ? `No ${cell ? LABEL[cell.type].toLowerCase() : 'events'} detected on ${cell?.tf} in the current analysis window.` : 'Awaiting analysis from the Market Structure Agent. AURUM never generates these events in the browser.'}
          </p>
        ) : (
          <ul className="flex flex-col">
            {cellEvents.map((e, i) => (
              <li key={i} className="flex items-center justify-between border-b border-line-soft py-2.5 text-sm last:border-0">
                <span className="num text-ink">{formatNumber(e.price, 5)}</span>
                <span className="flex items-center gap-2">
                  {e.direction && <Badge tone={e.direction === 'bullish' ? 'up' : 'down'}>{e.direction}</Badge>}
                  <span className="num text-xs text-neutral">{formatDateTime(e.time)} UTC</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Drawer>
    </div>
  );
}
