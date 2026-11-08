import { afterEach, describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import AIEnginePage from '@/pages/AIEnginePage';
import AISignalsPage from '@/pages/AISignalsPage';
import BacktestingPage from '@/pages/BacktestingPage';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { getDossier, getTradeIdeas } from '@/services/decisionService';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

const SIGNAL = {
  id: 'c1', instrument: 'EURUSD', direction: 'long', entryZone: [1.1, 1.101], stopLoss: 1.095, takeProfit: 1.11, riskReward: 2, strategy: 'order_block_continuation',
  timeframe: 'H1', marketCondition: 'TRENDING', confidence: 60, evidence: ['Structure agrees.'], timestamp: '2026-03-04T13:00:00+00:00', status: 'new',
};

const DOSSIER = {
  ...SIGNAL, takeProfit2: null, expiresAt: '2026-03-04T21:00:00+00:00', invalidation: 'Close below the stop.', grade: 'B', deterministicScore: 60, rejectedBy: null,
  reviewStatus: 'GENERATED', suggestedVolume: 0.1, volumeReason: null, evidenceDetail: [{ component: 'structure', points: 20, maxPoints: 25, sentence: 'Structure agrees.' }],
  gates: [{ gate: 'data_quality', result: 'pass', evidence: {}, checkedAt: '2026-03-04T13:00:00+00:00' }], aiReview: { status: 'unavailable', reason: 'disabled', verdict: null, adjustment: null, review: null },
  statusHistory: [{ from: null, to: 'new', actor: 'system', note: null, at: '2026-03-04T13:00:00+00:00' }], outcome: null, availability: { cot: 'available', account: 'missing' },
  disclaimer: 'Confidence measures how far the evidence agrees. It is not a probability.',
};

const ANALYSIS = {
  id: 'r1', symbol: 'EURUSD', asOf: '2026-03-04T13:00:00+00:00', status: 'ok', decision: 'WAIT', regime: 'TRENDING_UP', bias: 'bullish', candidates: 1, createdAt: '2026-03-04T13:00:00+00:00',
  availability: { cot: 'available' }, disclaimer: 'x',
  summary: {
    candidates: 1, decision: { decision: 'WAIT', reasons: ['Best candidate is grade C.'], candidateKey: null }, conflicts: [], evidence: [{ template: 'x', timeframe: 'H1', direction: 'long', finalScore: 50, grade: 'C', positive: [], negative: [], conflicting: [], missing: [], qualityDeductions: [] }],
    regime: { regime: 'TRENDING_UP', timeframe: 'H4', supporting: [], conflicting: [], quality: 'good' },
    bias: { overall: 'bullish', agreement: 0.8, agreeing: ['D1'], disagreeing: [], note: 'Higher timeframes set the context.', timeframes: { D1: { bias: 'bullish', weight: 5, state: 'trending', reason: null } } },
  },
};

afterEach(() => useAdminKeyStore.getState().clear());

describe('AI signals review', () => {
  it('keeps approval disabled without an admin key', async () => {
    mockApi({ 'GET /analysis/signals': [SIGNAL], 'GET /analysis/signals/c1': DOSSIER });
    renderWithProviders(<AISignalsPage />, { mock: false });
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled();
  });

  it('records a decision with the key in a header, and tells the operator no trade is placed', async () => {
    let sent: { key: unknown; body: unknown } | undefined;
    mockApi({
      'GET /analysis/signals': [SIGNAL],
      'GET /analysis/signals/c1': DOSSIER,
      'POST /analysis/signals/c1/approve': (config: { headers: Record<string, unknown>; data: string }) => {
        sent = { key: config.headers['X-AURUM-Admin-Key'], body: JSON.parse(config.data) };
        return { status: 200, data: {} };
      },
    });
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<AISignalsPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Approving records your decision. AURUM AI does not place trades.')).toBeInTheDocument();
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Note'), { target: { value: 'Clean structure.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record decision' }));
    await waitFor(() => expect(sent).toEqual({ key: 'secret', body: { reviewer: 'Test User', note: 'Clean structure.' } }));
  });

  it('says when the AI review is unavailable and what data is missing', async () => {
    mockApi({ 'GET /analysis/signals': [SIGNAL], 'GET /analysis/signals/c1': DOSSIER });
    renderWithProviders(<AISignalsPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Details' }));
    expect(await screen.findByText(/AI review unavailable \(Disabled\)/)).toBeInTheDocument();
    expect(screen.getByText('Account')).toBeInTheDocument();
    expect(screen.getByText(DOSSIER.disclaimer)).toBeInTheDocument();
  });

  it('never stores the admin key in browser storage', () => {
    act(() => useAdminKeyStore.getState().setKey('secret'));
    expect(JSON.stringify({ ...localStorage })).not.toContain('secret');
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('secret');
  });
});

describe('AI Engine', () => {
  it('shows the stored analysis', async () => {
    mockApi({ 'GET /ai/analysis': [ANALYSIS], 'GET /ai/analysis/r1': ANALYSIS });
    renderWithProviders(<AIEnginePage />, { mock: false });
    expect(await screen.findByText('Wait')).toBeInTheDocument();
    expect(screen.getByText('Trending up')).toBeInTheDocument();
    expect(screen.getByText('Best candidate is grade C.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Multi-Timeframe Analysis' }));
    expect(await screen.findByText('Higher timeframes set the context.')).toBeInTheDocument();
  });

  it('shows no invented analysis in mock mode, and cannot start a run', async () => {
    renderWithProviders(<AIEnginePage />);
    expect(await screen.findByText('No analysis yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run analysis/i })).toBeDisabled();
  });

  it('has no trade or order controls', async () => {
    renderWithProviders(<AIEnginePage />);
    await screen.findByText('No analysis yet');
    expect(screen.queryByRole('button', { name: /buy|sell|execute|send order|place/i })).not.toBeInTheDocument();
  });

  it('returns nothing from the decision services in mock mode', async () => {
    expect((await getDossier({ mock: true }, 'c1')).data).toBeNull();
    expect((await getTradeIdeas({ mock: true })).data).toEqual([]);
  });
});

describe('Backtesting', () => {
  it('lists registered strategies and starts a run with the key', async () => {
    let body: unknown;
    mockApi({
      'GET /ai/strategies': [{ id: 'bos_continuation', version: '1', enabled: true, requireAlignment: true, timeframes: ['H1'] }],
      'POST /backtests/run': (config: { data: string }) => {
        body = JSON.parse(config.data);
        return { status: 202, data: { jobId: 'j1', status: 'queued' } };
      },
      'GET /decision/jobs/j1': { jobId: 'j1', status: 'running' },
    });
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<BacktestingPage />, { mock: false });
    expect(await screen.findByRole('option', { name: 'Bos continuation' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /run backtest/i }));
    await waitFor(() => expect(body).toMatchObject({ symbols: ['EURUSD'] }));
    expect(await screen.findByText(/Job j1/)).toBeInTheDocument();
  });
});

describe('bundle safety', () => {
  it('sends no admin key from configuration', () => {
    const source = readFileSync(resolve(__dirname, '../services/decisionService.ts'), 'utf8');
    expect(source).not.toMatch(/import\.meta\.env/);
  });
});
