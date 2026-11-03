import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSettingsStore } from '@/stores/settingsStore';
import type { Sourced } from '@/types/api';
import type { Quote } from '@/types/market';

/** One demo tick: a small random walk on bid/ask that keeps the spread, high/low and change consistent. */
export function tickQuote(q: Quote, rand: () => number = Math.random): Quote {
  const point = 10 ** -q.digits;
  const step = Math.max(point, q.bid * 0.00025) * (rand() - 0.5) * 2;
  const bid = Number((q.bid + step).toFixed(q.digits));
  const ask = Number((bid + q.spread * point).toFixed(q.digits));
  const prevClose = q.bid / (1 + q.dailyChangePct / 100);
  return {
    ...q,
    bid,
    ask,
    dailyHigh: Math.max(q.dailyHigh, bid),
    dailyLow: Math.min(q.dailyLow, bid),
    dailyChangePct: ((bid - prevClose) / prevClose) * 100,
    updatedAt: new Date().toISOString(),
  };
}

/**
 * DEMO MODE ONLY: makes the clearly-labelled demo quotes move so the interface
 * can be evaluated "live". Never runs against backend data, pauses while the tab
 * is hidden, and can be switched off in Settings → Data.
 */
export function useDemoTicker(intervalMs = 2000): void {
  const qc = useQueryClient();
  const mock = useSettingsStore((s) => s.mockMode);
  const enabled = useSettingsStore((s) => s.demoTicks);
  useEffect(() => {
    if (!mock || !enabled) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      qc.setQueriesData<Sourced<Quote[]>>({ queryKey: ['quotes', true] }, (prev) =>
        prev && prev.source === 'demo' ? { ...prev, data: prev.data.map((q) => tickQuote(q)), fetchedAt: new Date().toISOString() } : prev,
      );
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [qc, mock, enabled, intervalMs]);
}
