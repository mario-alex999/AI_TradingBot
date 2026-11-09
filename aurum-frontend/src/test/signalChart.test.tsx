import { afterEach, describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import AISignalsPage from '@/pages/AISignalsPage';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { buildSignalDrawing, snapTime, tradingViewUrl, type SignalLike } from '@/components/signals/signalDrawing';
import { chartPalette } from '@/theme/theme';
import type { Candle, ChartOverlay } from '@/types/market';
import { createdCharts } from './lightweightChartsMock';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

const T0 = Date.parse('2026-03-04T10:00:00Z') / 1000;
const candles: Candle[] = Array.from({ length: 10 }, (_, i) => ({
  time: T0 + i * 3600, open: 1.1, high: 1.102, low: 1.099, close: 1.101, volume: 100,
}));

const LONG: SignalLike = {
  direction: 'long', entryZone: [1.1, 1.101], stopLoss: 1.095, takeProfit: 1.11, takeProfit2: 1.115, riskReward: 2,
  timestamp: '2026-03-04T13:20:00+00:00', grade: 'B',
};

const box = (d: ReturnType<typeof buildSignalDrawing>, id: string) => d.boxes.find((b) => b.id === id)!;

afterEach(() => useAdminKeyStore.getState().clear());

describe('signal drawing', () => {
  it('snaps a time to the bar that contains it', () => {
    const times = candles.map((c) => c.time);
    expect(snapTime(T0 + 3 * 3600 + 1200, times)).toBe(T0 + 3 * 3600);
    expect(snapTime(T0 - 99, times)).toBe(T0);
    expect(snapTime(T0 + 99 * 3600, times)).toBe(T0 + 9 * 3600);
    expect(snapTime(T0, [])).toBeNull();
  });

  it('draws a long as reward above the entry and risk below it, from the signal bar', () => {
    const d = buildSignalDrawing(LONG, candles, [], chartPalette(), 5);
    expect(d.signalTime).toBe(T0 + 3 * 3600);
    expect(box(d, 'reward')).toMatchObject({ top: 1.11, bottom: 1.1005, from: T0 + 3 * 3600, to: null });
    expect(box(d, 'risk')).toMatchObject({ top: 1.1005, bottom: 1.095 });
    expect(box(d, 'entry')).toMatchObject({ top: 1.101, bottom: 1.1 });
    expect(box(d, 'reward').label).toContain('2.00R');
    expect(d.levels.map((l) => l.title)).toEqual(['TP1', 'TP2', 'SL', 'Entry']);
    expect(d.markers).toEqual([expect.objectContaining({ shape: 'arrowUp', position: 'belowBar', text: 'LONG · B' })]);
  });

  it('flips the boxes for a short', () => {
    const d = buildSignalDrawing({ ...LONG, direction: 'short', stopLoss: 1.106, takeProfit: 1.09, takeProfit2: null }, candles, [], chartPalette(), 5);
    expect(box(d, 'reward')).toMatchObject({ top: 1.1005, bottom: 1.09 });
    expect(box(d, 'risk')).toMatchObject({ top: 1.106, bottom: 1.1005 });
    expect(d.markers[0]).toMatchObject({ shape: 'arrowDown', text: 'SHORT · B' });
    expect(d.levels.some((l) => l.id === 'tp2')).toBe(false);
  });

  it('draws backend structure only, and hides it on request', () => {
    const overlays: ChartOverlay[] = [
      { id: 'ob1', kind: 'order_block', label: 'H1 demand', price: 1.099, priceTo: 1.0995, from: T0 + 3600, direction: 'bullish' },
      { id: 'b1', kind: 'break_of_structure', label: 'up', price: 1.102, from: T0 + 2 * 3600, direction: 'bullish' },
      { id: 'pdh', kind: 'previous_day_hl', label: 'high', price: 1.104 },
    ];
    const on = buildSignalDrawing(LONG, candles, overlays, chartPalette(), 5);
    expect(box(on, 'ov-ob1')).toMatchObject({ top: 1.0995, bottom: 1.099, from: T0 + 3600 });
    expect(box(on, 'ov-b1')).toMatchObject({ top: 1.102, bottom: 1.102 });
    expect(on.markers.map((m) => m.text)).toContain('BOS');
    expect(on.levels.some((l) => l.id === 'ov-pdh')).toBe(true);
    const off = buildSignalDrawing(LONG, candles, overlays, chartPalette(), 5, { structure: false });
    expect(off.boxes.every((b) => !b.id.startsWith('ov-'))).toBe(true);
    expect(off.levels.every((l) => !l.id.startsWith('ov-'))).toBe(true);
  });

  it('marks a resolved outcome', () => {
    const d = buildSignalDrawing(
      { ...LONG, outcome: { touched: true, filled: true, result: 'target_hit', rMultiple: 2, mfeR: 2.1, maeR: -0.3, resolvedAt: '2026-03-04T17:30:00+00:00' } },
      candles, [], chartPalette(), 5,
    );
    expect(d.markers.at(-1)).toMatchObject({ time: T0 + 7 * 3600, text: 'target hit +2.00R' });
  });

  it('links to the same symbol and interval on TradingView', () => {
    expect(tradingViewUrl('EURUSD', 'H4')).toBe('https://www.tradingview.com/chart/?symbol=FX%3AEURUSD&interval=240');
    expect(tradingViewUrl('XAUUSD', 'M15')).toContain('OANDA%3AXAUUSD');
  });
});

const SIGNAL = {
  id: 'c1', instrument: 'EURUSD', direction: 'long', entryZone: [1.1, 1.101], stopLoss: 1.095, takeProfit: 1.11, riskReward: 2, strategy: 'order_block_continuation',
  timeframe: 'H1', marketCondition: 'TRENDING', confidence: 60, evidence: ['Structure agrees.'], timestamp: '2026-03-04T13:00:00+00:00', status: 'new',
};
const DOSSIER = {
  ...SIGNAL, takeProfit2: null, expiresAt: '2026-03-04T21:00:00+00:00', invalidation: 'Close below the stop.', grade: 'B', deterministicScore: 60, rejectedBy: null,
  reviewStatus: 'GENERATED', suggestedVolume: null, volumeReason: 'No account snapshot', evidenceDetail: [], gates: [],
  aiReview: { status: 'unavailable', reason: 'disabled', verdict: null, adjustment: null, review: null },
  statusHistory: [], outcome: null, availability: {}, disclaimer: 'Not a probability.',
};

describe('AI Signals chart analysis', () => {
  it('draws the candidate on a chart of stored candles in its dossier', async () => {
    mockApi({
      'GET /analysis/signals': [SIGNAL],
      'GET /analysis/signals/c1': DOSSIER,
      'GET /market/candles': candles,
      'GET /analysis/market-structure': [],
    });
    createdCharts.length = 0;
    renderWithProviders(<AISignalsPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Details' }));
    expect(await screen.findByTestId('signal-chart')).toBeInTheDocument();
    await waitFor(() => expect(createdCharts.at(-1)?.markers).toEqual([expect.objectContaining({ text: 'LONG · B' })]));
    const titles = (createdCharts.at(-1)!.priceLines as { title: string }[]).map((l) => l.title);
    expect(titles).toEqual(expect.arrayContaining(['TP1', 'SL', 'Entry']));
    expect(screen.getByRole('link', { name: /open in tradingview/i })).toHaveAttribute('href', expect.stringContaining('FX%3AEURUSD'));
  });

  it('runs an analysis with the admin key, and is unavailable without it', async () => {
    let sent: unknown;
    mockApi({
      'GET /analysis/signals': [],
      'POST /ai/analyze': (config: { headers: Record<string, unknown>; data: string }) => {
        sent = { key: config.headers['X-AURUM-Admin-Key'], body: JSON.parse(config.data) };
        return { status: 200, data: {} };
      },
    });
    const { unmount } = renderWithProviders(<AISignalsPage />, { mock: false });
    expect(await screen.findByRole('button', { name: /run analysis/i })).toBeDisabled();
    unmount();
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<AISignalsPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: /run analysis/i }));
    await waitFor(() => expect(sent).toEqual({ key: 'secret', body: { symbol: 'EURUSD', aiReview: true } }));
  });
});
