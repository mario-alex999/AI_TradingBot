import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import AISignalsPage from '@/pages/AISignalsPage';
import { outcomeState } from '@/config/signals';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

const base = {
  instrument: 'EURUSD', direction: 'short', entryZone: [1.1, 1.1002], entryPrice: 1.1001, stopLoss: 1.1022, takeProfit: 1.0951,
  riskReward: 2.38, strategy: 'fair_value_gap_retracement', timeframe: 'M15', marketCondition: 'TRENDING', confidence: 64,
  evidence: ['Structure agrees.'], timestamp: '2026-11-17T06:15:00+00:00', status: 'expired', grade: 'B', rejectedBy: null,
};
const outcome = (o: Record<string, unknown>) => ({ touched: false, filled: false, result: null, rMultiple: null, mfeR: null, maeR: null,
  filledAt: null, resolvedAt: null, exitPrice: null, ...o });

// Shapes as GET /analysis/signals returns them after the worker's outcome pass.
const SIGNALS = [
  { ...base, id: 'tp1', outcome: outcome({ state: 'take_profit', touched: true, filled: true, result: 'tp', rMultiple: 2.38,
    filledAt: '2026-11-17T06:40:00+00:00', resolvedAt: '2026-11-17T08:05:00+00:00', exitPrice: 1.0951 }) },
  { ...base, id: 'sl1', instrument: 'GBPUSD', outcome: outcome({ state: 'stop_loss', touched: true, filled: true, result: 'sl', rMultiple: -1,
    filledAt: '2026-11-17T06:30:00+00:00', resolvedAt: '2026-11-17T07:00:00+00:00', exitPrice: 1.1022 }) },
  { ...base, id: 'run1', instrument: 'XAUUSD', status: 'new', outcome: outcome({ state: 'running', touched: true, filled: true, result: 'open',
    filledAt: '2026-11-17T06:20:00+00:00' }) },
  { ...base, id: 'wait1', instrument: 'US30', status: 'new', outcome: outcome({ state: 'waiting', result: 'open' }) },
  { ...base, id: 'old1', outcome: null },
];

describe('signal outcomes on the AI Signals page', () => {
  it('shows on each card whether it triggered and hit the target or the stop, with entry, stop, target and exit', async () => {
    mockApi({ 'GET /analysis/signals': SIGNALS });
    renderWithProviders(<AISignalsPage />, { mock: false });
    const panels = await screen.findAllByTestId('signal-outcome');
    const byState = Object.fromEntries(panels.map((p) => [p.dataset.state, p]));
    const tp = byState.take_profit!;
    expect(within(tp).getByText('Take profit hit')).toBeInTheDocument();
    expect(within(tp).getByText('+2.38R')).toBeInTheDocument();
    expect(within(tp).getByText('Entry').nextSibling).toHaveTextContent('1.10010');
    expect(within(tp).getByText(/triggered 17 Nov 2026/i)).toBeInTheDocument();
    expect(within(tp).getByText('Exit').nextSibling).toHaveTextContent('1.09510');
    expect(within(byState.stop_loss!).getByText('Stop loss hit')).toBeInTheDocument();
    expect(within(byState.stop_loss!).getByText('−1.00R')).toBeInTheDocument();
    expect(within(byState.running!).getByText('Triggered · running')).toBeInTheDocument();
    expect(within(byState.waiting!).getByText('not reached yet')).toBeInTheDocument();
    expect(within(byState.pending!).getByText('Not checked yet')).toBeInTheDocument();
  });

  it('sums up the results and filters by them', async () => {
    mockApi({ 'GET /analysis/signals': SIGNALS });
    renderWithProviders(<AISignalsPage />, { mock: false });
    const results = await screen.findByRole('region', { name: 'Signal results' });
    expect(within(results).getByText('50%')).toBeInTheDocument();                       // 1 TP, 1 SL
    // tp1 and old1 are the same EURUSD short (same zone, same time): one idea
    expect(within(results).getByTestId('score-basis')).toHaveTextContent('4 ideas from 5 signals');
    fireEvent.click(within(results).getByRole('button', { name: /Take profit hit/ }));
    expect(screen.getAllByTestId('signal-outcome').map((p) => p.dataset.state)).toEqual(['take_profit']);
  });
});

describe('outcomeState', () => {
  it('reads older payloads without a state', () => {
    expect(outcomeState({ touched: true, filled: true, result: 'expired', rMultiple: 0.2, mfeR: null, maeR: null, resolvedAt: 'x' })).toBe('closed_at_limit');
    expect(outcomeState({ touched: false, filled: false, result: 'expired', rMultiple: null, mfeR: null, maeR: null, resolvedAt: 'x' })).toBe('not_triggered');
    expect(outcomeState(null)).toBe('pending');
  });
});

// ---- the scoreboard counts recommended setups, each idea once (22 Nov 2026 review)
const sl = (id: string, extra: Record<string, unknown>) => ({ ...base, id, ...extra,
  outcome: outcome({ state: 'stop_loss', touched: true, filled: true, result: 'sl', rMultiple: -1, resolvedAt: '2026-11-17T07:00:00+00:00' }) });

describe('honest scoreboard', () => {
  const MIXED = [
    { ...SIGNALS[0] },                                                                          // B, TP
    sl('dup1', { strategy: 'choch_reversal', entryZone: [1.2, 1.2002] }),                       // one B idea,
    sl('dup2', { strategy: 'order_block_continuation', entryZone: [1.2001, 1.2003] }),          // stored twice
    sl('d1', { instrument: 'XAUUSD', grade: 'D', entryZone: [3870, 3872] }),                     // research only
    sl('g1', { instrument: 'GBPUSD', rejectedBy: 'gate:event_risk', entryZone: [1.31, 1.3102] }), // blocked by a gate
  ];

  it('counts only recommended setups, and one idea once, by default', async () => {
    mockApi({ 'GET /analysis/signals': MIXED });
    renderWithProviders(<AISignalsPage />, { mock: false });
    const results = await screen.findByRole('region', { name: 'Signal results' });
    expect(within(results).getByTestId('score-basis')).toHaveTextContent('2 ideas from 3 signals');
    expect(within(results).getByTestId('band-split')).toHaveTextContent(/Recommended A\/B: 2 ideas, 50% win.*Research C\/D and rejected: 2 ideas, 0% win, -2R/);
    expect(within(results).getByText('50%')).toBeInTheDocument();                         // 1 TP vs 1 SL (not 3)
    fireEvent.click(within(results).getByRole('button', { name: 'All candidates' }));
    expect(within(results).getByTestId('score-basis')).toHaveTextContent('4 ideas from 5 signals');
    expect(within(results).getByText('25%')).toBeInTheDocument();                         // 1 TP vs 3 SL
  });
});
