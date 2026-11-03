import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LearningAgentPanel } from '@/components/agents/LearningAgentPanel';
import { renderWithProviders } from './render';
import { mockApi } from './mockApi';

const stats = (net: number, pf: number | null) => ({ trades: 10, wins: 6, net, expectancy: net / 10, profitFactor: pf, winRate: 60, tStat: 1.2 });

// Test fixture in the backend's response shape (not product data).
const lineage = {
  id: 'L1',
  status: 'terminated',
  createdAt: '2026-07-06T00:00:00+00:00',
  config: { discoveryWindow: 100, minPatternTrades: 20, minProfitFactor: 1.3, trialTrades: 10, trialMinProfitFactor: 1.2, maxFailedTrials: 3, maxGenerations: 20 },
  tradesObserved: 210,
  lastCloseTime: '2026-08-01T12:00:00+00:00',
  activeAgent: null,
  readOnly: true,
  agents: [
    {
      id: 'L1-G1', generation: 1, parent: null, status: 'replicated', createdAt: '2026-07-06T00:00:00+00:00', endedAt: '2026-07-20T00:00:00+00:00',
      pattern: null, discoveryStats: stats(400, 2.4), trialProgress: { matched: 0, seen: 0 }, failedTrials: 0,
      trials: [{ number: 1, pattern: [['session', 'london']], passed: true, inconclusive: false, stats: stats(120, 1.9), tickets: [], at: '2026-07-20T00:00:00+00:00' }],
      events: [{ at: '2026-07-20T00:00:00+00:00', kind: 'replicated', message: 'Replicated: L1-G2 (generation 2) continues the cycle.' }],
    },
    {
      id: 'L1-G2', generation: 2, parent: 'L1-G1', status: 'terminated', createdAt: '2026-07-20T00:00:00+00:00', endedAt: '2026-08-01T12:00:00+00:00',
      pattern: null, discoveryStats: null, trialProgress: { matched: 0, seen: 0 }, failedTrials: 3,
      trials: [1, 2, 3].map((n) => ({ number: n, pattern: [['side', 'buy']], passed: false, inconclusive: false, stats: stats(-80, 0.6), tickets: [], at: '2026-08-01T12:00:00+00:00' })),
      events: [{ at: '2026-08-01T12:00:00+00:00', kind: 'terminated', message: 'Terminated itself after 3 failed trials in a row. The lineage stops here.' }],
    },
  ],
};

describe('Trade Learning Agent panel', () => {
  it('explains the cycle in demo mode and never shows simulated agent results', async () => {
    renderWithProviders(<LearningAgentPanel />);
    expect(await screen.findByText('3 failures → terminate')).toBeInTheDocument();
    expect(screen.getByText(/never shows agent results/i)).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: /generations/i })).not.toBeInTheDocument();
  });

  it('shows the lineage from the backend and switches generation on click', async () => {
    mockApi({ 'GET /agents/learning': { lineage }, 'GET /health': { status: 'ok' } });
    const user = userEvent.setup();
    renderWithProviders(<LearningAgentPanel />, { mock: false });
    const gens = await screen.findByRole('navigation', { name: /generations/i });
    const g2 = within(gens).getByRole('button', { name: /G2/ });
    expect(g2).toHaveAttribute('aria-pressed', 'true'); // last agent selected when none is active
    expect(screen.getByLabelText('3 of 3 failed trials in a row')).toBeInTheDocument();
    expect(screen.getByText(/terminated itself after 3 failed trials/i)).toBeInTheDocument();
    await user.click(within(gens).getByRole('button', { name: /G1/ }));
    expect(screen.getByText(/Replicated: L1-G2/)).toBeInTheDocument();
    expect(screen.getByText('Passed')).toBeInTheDocument();
  });
});
