import { Badge } from '@/components/common/Badge';
import { OUTCOME, outcomeState } from '@/config/signals';
import { cn } from '@/utils/cn';
import { formatDateTime, formatNumber } from '@/utils/format';
import type { SignalOutcome as Outcome } from '@/types/trading';

interface Props {
  outcome: Outcome | null | undefined;
  entry: number;
  stopLoss: number;
  takeProfit: number;
  digits: number;
  /** compact: the card; full: the details drawer (adds best/worst excursion). */
  variant?: 'compact' | 'full';
}

function signedR(r: number): string {
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(2)}R`;
}

/**
 * What the signal did after it was published: waiting, triggered, take profit, stop loss... with the entry, stop and target
 * prices, when the entry was reached, and where and when it exited. Hypothetical: AURUM AI places no trades.
 */
export function SignalOutcomePanel({ outcome, entry, stopLoss, takeProfit, digits, variant = 'compact' }: Props) {
  const state = outcomeState(outcome);
  const meta = OUTCOME[state];
  const fmt = (v: number) => formatNumber(v, digits);
  const triggered = !!outcome?.filled;
  const exited = !!outcome?.resolvedAt && state !== 'not_triggered';
  const r = outcome?.rMultiple;
  const rows: [string, string, string?][] = [
    ['Entry', fmt(entry), triggered && outcome?.filledAt ? `triggered ${formatDateTime(outcome.filledAt)} UTC` : state === 'not_triggered' ? 'never reached' : 'not reached yet'],
    ['Stop loss', fmt(stopLoss), state === 'stop_loss' ? 'hit' : undefined],
    ['Take profit', fmt(takeProfit), state === 'take_profit' ? 'hit' : undefined],
  ];
  if (exited) {
    rows.push(['Exit', outcome?.exitPrice != null ? fmt(outcome.exitPrice) : '—', outcome?.resolvedAt ? `${formatDateTime(outcome.resolvedAt)} UTC` : undefined]);
  }
  return (
    <section
      aria-label="Signal outcome"
      data-testid="signal-outcome"
      data-state={state}
      className={cn(
        'flex flex-col gap-2 rounded-lg border p-3',
        state === 'take_profit' && 'border-up/40 bg-up-faint/50',
        (state === 'stop_loss' || state === 'ambiguous') && 'border-down/40 bg-down-faint/50',
        state === 'running' && 'border-gold/40 bg-gold-faint/50',
        !['take_profit', 'stop_loss', 'ambiguous', 'running'].includes(state) && 'border-line-soft bg-base-raised/40',
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span title={meta.hint}>
          <Badge tone={meta.tone}>{meta.label}</Badge>
        </span>
        {r != null && exited && (
          <span className={cn('num text-sm font-semibold', r > 0 ? 'text-up' : r < 0 ? 'text-down-text' : 'text-ink-muted')}>{signedR(r)}</span>
        )}
      </div>
      <dl className="grid grid-cols-[auto_auto_1fr] items-baseline gap-x-3 gap-y-1 text-xs">
        {rows.map(([k, v, note]) => (
          <div key={k} className="contents">
            <dt className="text-neutral">{k}</dt>
            <dd className="num text-ink">{v}</dd>
            <dd className="text-right text-2xs text-ink-muted">{note ?? ''}</dd>
          </div>
        ))}
      </dl>
      {variant === 'full' && triggered && (outcome?.mfeR != null || outcome?.maeR != null) && (
        <p className="text-2xs text-ink-muted">
          Best move in favour <span className="num text-ink">{outcome?.mfeR != null ? signedR(outcome.mfeR) : '—'}</span> · worst against{' '}
          <span className="num text-ink">{outcome?.maeR != null ? signedR(outcome.maeR) : '—'}</span>
        </p>
      )}
      <p className="text-2xs text-neutral">{meta.hint} Hypothetical result from stored M5 candles; AURUM AI places no trades.</p>
    </section>
  );
}
