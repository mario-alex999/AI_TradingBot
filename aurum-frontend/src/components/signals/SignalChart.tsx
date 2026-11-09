import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, LineStyle, type IChartApi, type IPriceLine, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp } from 'lightweight-charts';
import { Download, ExternalLink, Layers } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { Skeleton } from '@/components/common/Skeleton';
import { baseChartOptions, CHART_COLORS } from '@/components/charts/chartTheme';
import { BoxesPrimitive } from '@/components/charts/boxesPrimitive';
import { useCandles, useOverlays } from '@/hooks/queries';
import { chartPalette } from '@/theme/theme';
import { cn } from '@/utils/cn';
import type { Timeframe } from '@/types/market';
import { alpha, buildSignalDrawing, tradingViewUrl, type SignalLike } from './signalDrawing';

const BARS = 220;
/** Bars in view: enough context for the setup without shrinking the position boxes. */
const VISIBLE = 90;
const RIGHT_OFFSET = 24;
const LINE_STYLE = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted } as const;

interface Props {
  signal: SignalLike & { instrument: string; timeframe: Timeframe };
  digits: number;
  className?: string;
}

/**
 * Pictorial analysis of one trade candidate on a TradingView Lightweight Chart: stored candles, the position
 * (entry zone, reward and risk boxes, TP/SL levels), the signal marker and the backend's market structure.
 */
export function SignalChart({ signal, digits, className }: Props) {
  const candlesQ = useCandles(signal.instrument, signal.timeframe, BARS);
  const overlaysQ = useOverlays(signal.instrument, signal.timeframe);
  const [structure, setStructure] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const boxesRef = useRef<BoxesPrimitive | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

  const candles = useMemo(() => candlesQ.data?.data ?? [], [candlesQ.data]);
  const overlays = useMemo(() => overlaysQ.data?.data ?? [], [overlaysQ.data]);
  const drawing = useMemo(
    () => buildSignalDrawing(signal, candles, overlays, chartPalette(), digits, { structure }),
    [signal, candles, overlays, digits, structure],
  );
  const hasCandles = candles.length > 0;

  // Chart and candle series, once the candles are there.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !hasCandles) return;
    const chart = createChart(el, { ...baseChartOptions, timeScale: { ...baseChartOptions.timeScale, rightOffset: RIGHT_OFFSET } });
    const series = chart.addCandlestickSeries({
      upColor: CHART_COLORS.up,
      downColor: CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: CHART_COLORS.up,
      wickDownColor: CHART_COLORS.down,
      priceFormat: { type: 'price', precision: digits, minMove: 10 ** -digits },
    });
    const boxes = new BoxesPrimitive();
    series.attachPrimitive(boxes);
    chartRef.current = chart;
    seriesRef.current = series;
    boxesRef.current = boxes;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      boxesRef.current = null;
      linesRef.current = [];
    };
  }, [hasCandles, digits]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    series.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
    // Keep the trade in view: the levels may sit outside the recent price range.
    series.applyOptions({
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = original();
        if (!r) return r;
        const prices = [signal.stopLoss, signal.takeProfit, ...signal.entryZone, ...(signal.takeProfit2 != null ? [signal.takeProfit2] : [])];
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...prices), maxValue: Math.max(r.priceRange.maxValue, ...prices) } };
      },
    });
    // Zoom to the recent bars and the space to the right where the position is projected (scroll to see more).
    chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, candles.length - VISIBLE), to: candles.length - 1 + RIGHT_OFFSET });
  }, [candles, signal]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    boxesRef.current?.setBoxes(drawing.boxes);
    linesRef.current.forEach((l) => series.removePriceLine(l));
    linesRef.current = drawing.levels.map((l) =>
      series.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LINE_STYLE[l.style], axisLabelVisible: !l.id.startsWith('ov-'), title: l.title }),
    );
    series.setMarkers(drawing.markers.map((m) => ({ ...m, time: m.time as UTCTimestamp })) as SeriesMarker<Time>[]);
  }, [drawing]);

  const exportPng = () => {
    const canvas = chartRef.current?.takeScreenshot();
    if (!canvas) return;
    const a = document.createElement('a');
    a.href = canvas.toDataURL('image/png');
    a.download = `aurum_${signal.instrument}_${signal.timeframe}_${signal.direction}_signal.png`;
    a.click();
  };

  const p = chartPalette();
  const key = [
    { label: 'Entry zone', color: alpha(p.accent, 0.5) },
    { label: 'Reward', color: alpha(p.up, 0.4) },
    { label: 'Risk', color: alpha(p.down, 0.4) },
    ...(structure && overlays.length ? [{ label: 'Market structure (Module 03)', color: alpha(p.grey1, 0.4) }] : []),
  ];

  return (
    <div className={cn('flex flex-col gap-2', className)} data-testid="signal-chart">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-neutral">
          <span className="num text-ink">{signal.instrument}</span> · {signal.timeframe} · {candles.length} bars · UTC
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            icon={<Layers className="h-3.5 w-3.5" />}
            aria-pressed={structure}
            onClick={() => setStructure((v) => !v)}
            disabled={overlays.length === 0}
            disabledReason="No Module 03 structure stored for this instrument and timeframe"
          >
            Structure {structure && overlays.length ? 'on' : 'off'}
          </Button>
          <Button size="sm" variant="secondary" icon={<Download className="h-3.5 w-3.5" />} onClick={exportPng} disabled={!hasCandles} disabledReason="No candles to export">
            PNG
          </Button>
          <a
            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-control px-3 text-xs text-ink hover:bg-base-raised"
            href={tradingViewUrl(signal.instrument, signal.timeframe)}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden /> Open in TradingView
          </a>
        </div>
      </div>
      <div className="relative h-[440px] w-full overflow-hidden rounded-lg border border-line bg-base-card">
        {candlesQ.isPending ? (
          <Skeleton className="absolute inset-0" />
        ) : !hasCandles ? (
          <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-xs text-ink-muted">
            No stored {signal.timeframe} candles for {signal.instrument}. The trade levels are listed below; run a collection to see them on the chart.
          </div>
        ) : null}
        <div ref={containerRef} className="absolute inset-0" aria-label={`${signal.instrument} ${signal.timeframe} chart with the ${signal.direction} setup drawn`} role="img" />
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-muted">
        {key.map((k) => (
          <li key={k.label} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-3.5 rounded-sm" style={{ background: k.color }} aria-hidden />
            {k.label}
          </li>
        ))}
        <li className="ml-auto">Drawn on TradingView Lightweight Charts from stored data</li>
      </ul>
    </div>
  );
}
