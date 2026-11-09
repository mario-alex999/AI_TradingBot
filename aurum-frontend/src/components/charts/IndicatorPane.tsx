import { useEffect, useRef } from 'react';
import { createChart, LineStyle, type IChartApi, type LogicalRange, type UTCTimestamp } from 'lightweight-charts';
import type { Candle } from '@/types/market';
import { baseChartOptions, CHART_COLORS } from './chartTheme';
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

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      ...baseChartOptions,
      timeScale: { ...baseChartOptions.timeScale, visible: false },
      handleScroll: false,
      handleScale: false,
    });
    chartRef.current = chart;
    pane.lines.forEach((spec, idx) => {
      const data = candles.map((c, i) => (spec.values[i] == null ? { time: c.time as UTCTimestamp } : { time: c.time as UTCTimestamp, value: spec.values[i]! }));
      if (spec.kind === 'histogram') {
        const s = chart.addHistogramSeries({ color: CHART_COLORS.reference, priceLineVisible: false, lastValueVisible: false });
        s.setData(
          data.map((d) =>
            'value' in d && d.value !== undefined
              ? { ...d, color: d.value >= 0 ? CHART_COLORS.upHist : CHART_COLORS.downHist }
              : { time: d.time },
          ),
        );
      } else {
        const s = chart.addLineSeries({ color: spec.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: idx === 0 });
        s.setData(data);
        if (idx === 0)
          pane.levels?.forEach((lv) => s.createPriceLine({ price: lv, color: CHART_COLORS.reference, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' }));
      }
    });
    const unsub = subscribeRange?.((r) => r && chart.timeScale().setVisibleLogicalRange(r));
    return () => {
      unsub?.();
      chart.remove();
      chartRef.current = null;
    };
  }, [pane, candles, subscribeRange]);

  return (
    <div className="border-t border-line">
      <p className="num px-3 pt-1.5 text-2xs text-neutral">{pane.title}</p>
      <div className="relative h-28 w-full" data-testid={`pane-${pane.id}`}>
        <div ref={el} className="absolute inset-0" />
      </div>
    </div>
  );
}
