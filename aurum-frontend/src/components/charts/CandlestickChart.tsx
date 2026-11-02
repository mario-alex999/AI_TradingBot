import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import {
  createChart,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LogicalRange,
  type UTCTimestamp,
  type CandlestickData,
} from 'lightweight-charts';
import type { Candle, ChartOverlay } from '@/types/market';
import { formatNumber } from '@/utils/format';
import { baseChartOptions, CHART_COLORS } from './chartTheme';
import type { LineSpec } from './indicatorSeries';

export interface CandlestickChartHandle {
  reset: () => void;
  exportPng: (filename: string) => void;
  subscribeRange: (cb: (r: LogicalRange | null) => void) => () => void;
}

interface Props {
  candles: Candle[];
  digits: number;
  lines?: LineSpec[];
  overlays?: ChartOverlay[];
  showVolume?: boolean;
  watermark?: string;
  className?: string;
}

interface Legend {
  o: number;
  h: number;
  l: number;
  c: number;
}

export const CandlestickChart = forwardRef<CandlestickChartHandle, Props>(function CandlestickChart(
  { candles, digits, lines = [], overlays = [], showVolume = true, watermark, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const lineRefs = useRef<ISeriesApi<'Line'>[]>([]);
  const priceLineRefs = useRef<IPriceLine[]>([]);
  const [legend, setLegend] = useState<Legend | null>(null);

  // Create the chart once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, baseChartOptions);
    const candleSeries = chart.addCandlestickSeries({
      upColor: CHART_COLORS.up,
      downColor: CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: CHART_COLORS.up,
      wickDownColor: CHART_COLORS.down,
    });
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.subscribeCrosshairMove((p) => {
      const d = p.seriesData.get(candleSeries) as CandlestickData | undefined;
      setLegend(d ? { o: d.open, h: d.high, l: d.low, c: d.close } : null);
    });
    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volume;
    return () => {
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      lineRefs.current = [];
      priceLineRefs.current = [];
    };
  }, []);

  // Price data.
  useEffect(() => {
    const cs = candleRef.current;
    const vs = volumeRef.current;
    if (!cs || !vs) return;
    const minMove = 10 ** -digits;
    cs.applyOptions({ priceFormat: { type: 'price', precision: digits, minMove } });
    cs.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
    vs.setData(
      showVolume
        ? candles.map((c) => ({
            time: c.time as UTCTimestamp,
            value: c.volume,
            color: c.close >= c.open ? 'rgba(22,199,132,0.35)' : 'rgba(234,57,67,0.35)',
          }))
        : [],
    );
  }, [candles, digits, showVolume]);

  // Indicator lines on the price pane.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    lineRefs.current.forEach((s) => chart.removeSeries(s));
    lineRefs.current = lines.map((spec) => {
      const s = chart.addLineSeries({
        color: spec.color,
        lineWidth: 1,
        lineStyle: spec.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        title: '',
      });
      s.setData(candles.map((c, i) => (spec.values[i] == null ? { time: c.time as UTCTimestamp } : { time: c.time as UTCTimestamp, value: spec.values[i]! })));
      return s;
    });
  }, [lines, candles]);

  // Backend-supplied overlays (levels and zones). Never computed client-side.
  useEffect(() => {
    const cs = candleRef.current;
    if (!cs) return;
    priceLineRefs.current.forEach((l) => cs.removePriceLine(l));
    priceLineRefs.current = overlays.flatMap((o) => {
      const color = o.direction === 'bearish' ? CHART_COLORS.down : o.direction === 'bullish' ? CHART_COLORS.up : CHART_COLORS.gold;
      const mk = (price: number, title: string) =>
        cs.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title });
      return o.priceTo != null ? [mk(o.price, o.label), mk(o.priceTo, '')] : [mk(o.price, o.label)];
    });
  }, [overlays]);

  useImperativeHandle(
    ref,
    () => ({
      reset: () => {
        chartRef.current?.timeScale().fitContent();
        chartRef.current?.priceScale('right').applyOptions({ autoScale: true });
      },
      exportPng: (filename) => {
        const canvas = chartRef.current?.takeScreenshot();
        if (!canvas) return;
        const a = document.createElement('a');
        a.href = canvas.toDataURL('image/png');
        a.download = filename;
        a.click();
      },
      subscribeRange: (cb) => {
        const ts = chartRef.current?.timeScale();
        if (!ts) return () => undefined;
        ts.subscribeVisibleLogicalRangeChange(cb);
        return () => ts.unsubscribeVisibleLogicalRangeChange(cb);
      },
    }),
    [],
  );

  const last = candles[candles.length - 1];
  const shown = legend ?? (last ? { o: last.open, h: last.high, l: last.low, c: last.close } : null);

  return (
    <div className={className ?? 'relative h-[460px] w-full'} data-testid="candlestick-chart">
      <div ref={containerRef} className="absolute inset-0" />
      {shown && (
        <div className="num pointer-events-none absolute left-3 top-2 z-10 flex flex-wrap gap-x-3 text-2xs text-ink-muted">
          {(['o', 'h', 'l', 'c'] as const).map((k) => (
            <span key={k}>
              {k.toUpperCase()} <span className={shown.c >= shown.o ? 'text-up' : 'text-down-text'}>{formatNumber(shown[k], digits)}</span>
            </span>
          ))}
          {lines.map((l) => (
            <span key={l.id} style={{ color: l.color }}>
              {l.label}
            </span>
          ))}
        </div>
      )}
      {watermark && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <span className="select-none text-3xl font-bold uppercase tracking-[0.3em] text-white/[0.05] sm:text-5xl">{watermark}</span>
        </div>
      )}
    </div>
  );
});
