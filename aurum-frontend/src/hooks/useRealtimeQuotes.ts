import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { env } from '@/config/env';
import { subscribeToQuotes } from '@/services/realtime';
import { useSettingsStore } from '@/stores/settingsStore';
import type { Sourced } from '@/types/api';
import type { Quote } from '@/types/market';

/**
 * Applies streamed quotes to the TanStack Query cache, so components
 * update without polling. Active only when VITE_REALTIME_URL is set and
 * mock mode is off.
 */
export function useRealtimeQuotes(): void {
  const qc = useQueryClient();
  const mock = useSettingsStore((s) => s.mockMode);
  useEffect(() => {
    if (mock || !env.realtimeUrl) return;
    return subscribeToQuotes(env.realtimeUrl, (q) => {
      qc.setQueriesData<Sourced<Quote[]>>({ queryKey: ['quotes', false] }, (prev) =>
        prev ? { ...prev, data: prev.data.map((x) => (x.symbol === q.symbol ? q : x)), fetchedAt: new Date().toISOString() } : prev,
      );
    });
  }, [qc, mock]);
}
