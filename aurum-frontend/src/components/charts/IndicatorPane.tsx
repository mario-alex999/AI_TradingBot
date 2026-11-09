import { useEffect, useRef } from 'react';
import { createChart, LineStyle, type IChartApi, type LogicalRange, type UTCTimestamp } from 'lightweight-charts';
import type { Candle } from '@/types/market';
import { useSettingsStore } from '@/stores/settingsStore';
import { baseChartOptions, chartOptions, chartPalette } from './chartTheme';
import type { PaneSpec } from './indicatorSeries';

interface Props {
  pane: PaneSpec;
  candles: Candle[];
  subscribeRange?: (cb: (r: LogicalRange | null) => void) => () => void;
}

/** Secondary indicator pane (RSI / MACD / ATR) time-synchronised with the main chart. */
export function IndicatorPane({ pane, candles, subscribeRange }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const theme = useSettingsStore((s) => s.theme);

  useEffect(() => {
    if (!el.current) return;
    const colors = chartPalette(theme);
    const themed = chartOptions(theme);
    const chart = createChart(el.current, {
      ...baseChartOptions,
      ...themed,
      timeScale: { ...baseChartOptions.timeScale, ...themed.timeScale, visible: false },
      handleScroll: false,
      handleScale: false,
    });
    chartRef.current = chart;
    pane.lines.forEach((spec, idx) => {
      const data = candles.map((c, i) => (spec.values[i] == null ? { time: c.time as UTCTimestamp } : { time: c.time as UTCTimestamp, value: spec.values[i]! }));
      if (spec.kind === 'histogram') {
        const s = chart.addHistogramSeries({ color: colors.level, priceLineVisible: false, lastValueVisible: false });
        s.setData(
          data.map((d) =>
            'value' in d && d.value !== undefined
              ? { ...d, color: d.value >= 0 ? colors.upHist : colors.downHist }
              : { time: d.time },
          ),
        );
      } else {
        const s = chart.addLineSeries({ color: spec.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: idx === 0 });
        s.setData(data);
        if (idx === 0)
          pane.levels?.forEach((lv) => s.createPriceLine({ price: lv, color: colors.level, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' }));
      }
    });
    const unsub = subscribeRange?.((r) => r && chart.timeScale().setVisibleLogicalRange(r));
    return () => {
      unsub?.();
      chart.remove();
      chartRef.current = null;
    };
  }, [pane, candles, subscribeRange, theme]);

  return (
    <div className="border-t border-line">
      <p className="num px-3 pt-1.5 text-2xs text-neutral">{pane.title}</p>
      <div className="relative h-28 w-full" data-testid={`pane-${pane.id}`}>
        <div ref={el} className="absolute inset-0" />
      </div>
    </div>
  );
}
