import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import AISignalsPage from '@/pages/AISignalsPage';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { missingInputs } from '@/utils/availability';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

// Shapes copied from the live API (GET /analysis/signals/{id}, 17 Nov 2026): availability is an object per input,
// gates carry nested evidence, evidence carries sources. The drawer went black on exactly this payload.
const SIGNAL = {
  id: 'live1', instrument: 'XAUUSD', direction: 'short', entryZone: [3875.05944, 3886.79998], stopLoss: 3904.171, takeProfit: 3832.241,
  riskReward: 2.09, strategy: 'premium_discount_ote', timeframe: 'H1', marketCondition: 'RANGING', confidence: 60,
  evidence: ['H1 Module 03 grade none; displacement; zone fresh.'], timestamp: '2026-11-16T12:47:28+00:00', status: 'new',
};

const DOSSIER = {
  ...SIGNAL, takeProfit2: null, expiresAt: '2026-11-16T22:47:28+00:00', invalidation: 'A H1 close above 3904.17100, or no fill by 2026-11-16 22:47 UTC.',
  grade: 'A', deterministicScore: 75.6, rejectedBy: null, reviewStatus: 'GENERATED', suggestedVolume: 0.01,
  volumeReason: 'The minimum lot 0.01 risks 4.62% of equity, more than the 0.5% requested.',
  evidenceDetail: [
    { component: 'setup_quality', points: 15.6, maxPoints: 30, sentence: 'H1 Module 03 grade none; displacement; zone fresh.',
      sources: [{ kind: 'OTE band', module: 'module_03', findingId: null, sourceTime: null, confirmationTime: null }] },
    { component: 'data_quality', points: -5, maxPoints: 0, sentence: 'Missing inputs: economic_calendar.', sources: [] },
  ],
  gates: [
    { gate: 'data_quality', result: 'pass', evidence: { problems: [] }, thresholds: { maxInvalidCandles: 0 }, checkedAt: '2026-11-16T12:47:28+00:00' },
    { gate: 'event_risk', result: 'warn', evidence: { state: 'UNKNOWN', coverage: { gaps: { USD: ['Fed speeches'] } } }, thresholds: { confidenceCap: 60 },
      checkedAt: '2026-11-16T12:47:28+00:00' },
  ],
  aiReview: { status: 'unavailable', reason: 'disabled', verdict: null, adjustment: null, review: null, model: null, promptVersion: '1',
    createdAt: '2026-11-16T12:47:34.483038+00:00' },
  statusHistory: [{ from: null, to: 'new', actor: 'system', note: null, at: '2026-11-16T12:47:28+00:00' }],
  outcome: null,
  availability: {
    spread: { reason: null, available: true },
    account: { reason: null, available: true, capturedAt: '2026-11-16T12:35:31.214388+00:00' },
    learning: { reason: 'No finished Learning Agent trials.', available: false },
    module03H1: { reason: null, available: true, lastCandle: '2026-11-16T11:00:00+00:00' },
    economicCalendar: { level: 'official', reason: 'Official calendars do not cover USD for XAUUSD.', available: false },
  },
  disclaimer: 'Trade candidates for human review on a demo account. Confidence is agreement of evidence, not a probability of profit. AURUM AI does not place trades.',
};

describe('signal details', () => {
  it('opens the live dossier shape instead of a blank screen', async () => {
    mockApi({ 'GET /analysis/signals': [SIGNAL], 'GET /analysis/signals/live1': DOSSIER });
    renderWithProviders(<AISignalsPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Details' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(DOSSIER.invalidation)).toBeInTheDocument();
    expect(within(dialog).getByText('Grade A')).toBeInTheDocument();
    expect(within(dialog).getByText('1 : 2.09')).toBeInTheDocument();
    // only the inputs that were missing, each with the backend's reason
    expect(within(dialog).getByText('Learning')).toBeInTheDocument();
    expect(within(dialog).getByText('No finished Learning Agent trials.')).toBeInTheDocument();
    expect(within(dialog).getByText('Official calendars do not cover USD for XAUUSD.')).toBeInTheDocument();
    expect(within(dialog).queryByText('Spread')).not.toBeInTheDocument();
    expect(within(dialog).getByText('Warning')).toBeInTheDocument();
  });

  it('shows the details even when a gate result is new to the dashboard', async () => {
    const odd = { ...DOSSIER, gates: [{ ...DOSSIER.gates[0], result: 'skipped' }], riskReward: null };
    mockApi({ 'GET /analysis/signals': [SIGNAL], 'GET /analysis/signals/live1': odd });
    renderWithProviders(<AISignalsPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Details' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Skipped')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Unavailable').length).toBeGreaterThan(0);
  });
});

describe('missingInputs', () => {
  it('accepts both the object shape and the older word shape', () => {
    expect(missingInputs({ a: { available: true, reason: null }, b: { available: false, reason: 'x' }, c: 'available', d: 'missing', e: null })).toEqual([
      { key: 'b', reason: 'x' },
      { key: 'd', reason: 'missing' },
      { key: 'e', reason: null },
    ]);
    expect(missingInputs(undefined)).toEqual([]);
  });
});

describe('ErrorBoundary', () => {
  it('shows a message in place of a broken panel and keeps the rest of the page', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    function Broken(): JSX.Element {
      throw new Error('boom');
    }
    renderWithProviders(
      <div>
        <p>Still here</p>
        <ErrorBoundary what="this panel">
          <Broken />
        </ErrorBoundary>
      </div>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Could not show this panel.');
    expect(screen.getByText('Still here')).toBeInTheDocument();
    spy.mockRestore();
  });
});
