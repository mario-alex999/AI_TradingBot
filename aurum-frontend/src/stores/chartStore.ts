import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { OverlayKind, Timeframe } from '@/types/market';

export type IndicatorId = 'sma' | 'ema' | 'bollinger' | 'vwap' | 'rsi' | 'macd' | 'atr';

export interface IndicatorConfig {
  enabled: boolean;
  period?: number;
}

interface ChartState {
  instrument: string;
  timeframe: Timeframe;
  indicators: Record<IndicatorId, IndicatorConfig>;
  overlays: Partial<Record<OverlayKind, boolean>>;
  showVolume: boolean;
  setInstrument: (s: string) => void;
  setTimeframe: (t: Timeframe) => void;
  toggleIndicator: (id: IndicatorId) => void;
  setIndicatorPeriod: (id: IndicatorId, period: number) => void;
  toggleOverlay: (k: OverlayKind) => void;
  setShowVolume: (v: boolean) => void;
}

export const DEFAULT_INDICATORS: Record<IndicatorId, IndicatorConfig> = {
  sma: { enabled: false, period: 50 },
  ema: { enabled: false, period: 20 },
  bollinger: { enabled: false, period: 20 },
  vwap: { enabled: false },
  rsi: { enabled: false, period: 14 },
  macd: { enabled: false },
  atr: { enabled: false, period: 14 },
};

export const useChartStore = create<ChartState>()(
  persist(
    (set) => ({
      instrument: 'EURUSD',
      timeframe: 'H1',
      indicators: DEFAULT_INDICATORS,
      overlays: {},
      showVolume: true,
      setInstrument: (instrument) => set({ instrument }),
      setTimeframe: (timeframe) => set({ timeframe }),
      toggleIndicator: (id) =>
        set((s) => ({ indicators: { ...s.indicators, [id]: { ...s.indicators[id], enabled: !s.indicators[id].enabled } } })),
      setIndicatorPeriod: (id, period) =>
        set((s) => ({ indicators: { ...s.indicators, [id]: { ...s.indicators[id], period } } })),
      toggleOverlay: (k) => set((s) => ({ overlays: { ...s.overlays, [k]: !s.overlays[k] } })),
      setShowVolume: (showVolume) => set({ showVolume }),
    }),
    { name: 'aurum.chart', storage: createJSONStorage(() => localStorage) },
  ),
);
