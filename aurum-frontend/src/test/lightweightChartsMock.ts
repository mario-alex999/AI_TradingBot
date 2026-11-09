import { vi } from 'vitest';

function series() {
  return {
    setData: vi.fn(),
    applyOptions: vi.fn(),
    createPriceLine: vi.fn(() => ({})),
    removePriceLine: vi.fn(),
    setMarkers: vi.fn(),
    attachPrimitive: vi.fn(),
    detachPrimitive: vi.fn(),
    priceToCoordinate: vi.fn(() => 0),
    priceScale: () => ({ applyOptions: vi.fn() }),
  };
}

export const createdCharts: { candleData: unknown[]; markers: unknown[]; priceLines: unknown[] }[] = [];

export function createChart() {
  const record = { candleData: [] as unknown[], markers: [] as unknown[], priceLines: [] as unknown[] };
  createdCharts.push(record);
  const timeScale = {
    fitContent: vi.fn(),
    setVisibleLogicalRange: vi.fn(),
    subscribeVisibleLogicalRangeChange: vi.fn(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(),
  };
  return {
    addCandlestickSeries: () => {
      const s = series();
      s.setData = vi.fn((d: unknown[]) => {
        record.candleData = d;
      });
      s.setMarkers = vi.fn((m: unknown[]) => {
        record.markers = m;
      });
      s.createPriceLine = vi.fn((l: unknown) => {
        record.priceLines.push(l);
        return {};
      });
      return s;
    },
    addHistogramSeries: series,
    addLineSeries: series,
    removeSeries: vi.fn(),
    subscribeCrosshairMove: vi.fn(),
    timeScale: () => timeScale,
    priceScale: () => ({ applyOptions: vi.fn() }),
    takeScreenshot: vi.fn(() => document.createElement('canvas')),
    remove: vi.fn(),
  };
}

export const ColorType = { Solid: 'solid' };
export const CrosshairMode = { Normal: 0 };
export const LineStyle = { Solid: 0, Dotted: 1, Dashed: 2 };
