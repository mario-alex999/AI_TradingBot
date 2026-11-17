import type { Tone } from '@/components/common/Badge';
import type { OutcomeState, SignalOutcome, SignalStatus } from '@/types/trading';

export const SIGNAL_STATUS: Record<SignalStatus, { label: string; tone: Tone }> = {
  new: { label: 'New', tone: 'gold' },
  under_review: { label: 'Under review', tone: 'info' },
  approved: { label: 'Approved', tone: 'up' },
  rejected: { label: 'Rejected', tone: 'down' },
  expired: { label: 'Expired', tone: 'neutral' },
  executed: { label: 'Executed', tone: 'up' },
};

/** What a signal did in the market after it was published. Hypothetical: AURUM AI places no trades. */
export const OUTCOME: Record<OutcomeState, { label: string; tone: Tone; hint: string }> = {
  pending: { label: 'Not checked yet', tone: 'neutral', hint: 'Outcomes are checked every 5 minutes from stored candles.' },
  waiting: { label: 'Waiting for entry', tone: 'info', hint: 'Price has not reached the entry yet.' },
  running: { label: 'Triggered · running', tone: 'gold', hint: 'Price reached the entry; neither target nor stop hit yet.' },
  take_profit: { label: 'Take profit hit', tone: 'up', hint: 'Price reached the entry, then the target.' },
  stop_loss: { label: 'Stop loss hit', tone: 'down', hint: 'Price reached the entry, then the stop.' },
  ambiguous: { label: 'Stop or target (unclear)', tone: 'warn', hint: 'Target and stop traded in the same candle; counted as a loss.' },
  closed_at_limit: { label: 'Closed at time limit', tone: 'neutral', hint: 'Triggered, but neither level was hit within the holding limit.' },
  not_triggered: { label: 'Not triggered', tone: 'neutral', hint: 'The signal expired before price reached the entry.' },
};

/** The state, also for an older payload without ``state``. */
export function outcomeState(o: SignalOutcome | null | undefined): OutcomeState {
  if (!o) return 'pending';
  if (o.state) return o.state;
  if (o.result === 'tp') return 'take_profit';
  if (o.result === 'sl') return 'stop_loss';
  if (o.result === 'ambiguous') return 'ambiguous';
  if (o.result === 'expired') return o.filled ? 'closed_at_limit' : 'not_triggered';
  if (o.result === 'open') return o.filled ? 'running' : 'waiting';
  return 'pending';
}

/** Finished with an exit (win, loss or time limit). */
export const RESOLVED: OutcomeState[] = ['take_profit', 'stop_loss', 'ambiguous', 'closed_at_limit'];
