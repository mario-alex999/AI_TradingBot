import { describe, expect, it } from 'vitest';
import { distinctIdeas, isRecommended, scoreboard } from '@/utils/signalScore';
import type { TradeSignal } from '@/types/trading';

const sig = (o: Partial<TradeSignal>): TradeSignal => ({
  id: 'x', instrument: 'EURUSD', direction: 'short', entryZone: [1.10175, 1.10194], stopLoss: 1.1021, takeProfit: 1.10116, riskReward: 2.8,
  strategy: 'order_block_continuation', timeframe: 'M15', marketCondition: 'TRENDING', confidence: 50, evidence: [],
  timestamp: '2026-11-16T07:32:06+00:00', status: 'rejected', grade: 'B', rejectedBy: null, ...o,
});

describe('which signals count', () => {
  it('recommends grade A/B unless a gate or the AI reviewer rejected it', () => {
    expect(isRecommended(sig({}))).toBe(true);
    expect(isRecommended(sig({ grade: 'C' }))).toBe(false);
    expect(isRecommended(sig({ rejectedBy: 'gate:duplication' }))).toBe(false);
    expect(isRecommended(sig({ rejectedBy: 'ai_reviewer' }))).toBe(false);
    expect(isRecommended(sig({ rejectedBy: 'ann' }))).toBe(true);                    // a person's call: still recommended
  });

  it('treats the 16 Nov EURUSD triple (three strategies, one zone) as one idea', () => {
    const triple = [sig({ id: 'a' }), sig({ id: 'b', strategy: 'choch_reversal' }), sig({ id: 'c', entryZone: [1.10175, 1.10186] })];
    expect(distinctIdeas(triple).map((s) => s.id)).toEqual(['a']);
  });

  it('keeps ideas apart by direction, instrument, zone and time', () => {
    const list = [
      sig({ id: 'a' }),
      sig({ id: 'long', direction: 'long' }),
      sig({ id: 'gbp', instrument: 'GBPUSD' }),
      sig({ id: 'far', entryZone: [1.105, 1.106] }),
      sig({ id: 'later', timestamp: '2026-11-16T12:00:00+00:00' }),
    ];
    expect(distinctIdeas(list).map((s) => s.id).sort()).toEqual(['a', 'far', 'gbp', 'later', 'long']);
  });

  it('adds up R over finished trades only', () => {
    const b = scoreboard(
      [
        sig({ id: 'w', outcome: { state: 'take_profit', touched: true, filled: true, result: 'tp', rMultiple: 2.5, mfeR: null, maeR: null, resolvedAt: 'x' } }),
        sig({ id: 'l', instrument: 'GBPUSD', outcome: { state: 'stop_loss', touched: true, filled: true, result: 'sl', rMultiple: -1, mfeR: null, maeR: null, resolvedAt: 'x' } }),
        sig({ id: 'r', instrument: 'XAUUSD', outcome: { state: 'running', touched: true, filled: true, result: 'open', rMultiple: null, mfeR: 0.4, maeR: null, resolvedAt: null } }),
      ],
      'recommended',
    );
    expect(b).toMatchObject({ wins: 1, losses: 1, winRate: 50, totalR: 1.5, ideas: 3, signals: 3 });
  });
});
