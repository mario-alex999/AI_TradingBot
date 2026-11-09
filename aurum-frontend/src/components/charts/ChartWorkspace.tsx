import type { ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Card } from '@/components/common/Card';
import { cn } from '@/utils/cn';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { EmptyState } from '@/components/common/EmptyState';
import { Skeleton } from '@/components/common/Skeleton';
import { useCandles, useInstruments, useOverlays } from '@/hooks/queries';
import { useChartStore } from '@/stores/chartStore';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { demoProfile } from '@/mocks/market';
import { toast } from '@/stores/toastStore';
import type { Timeframe } from '@/types/market';
import { CandlestickChart, type CandlestickChartHandle } from './CandlestickChart';
import { ChartToolbar } from './ChartToolbar';
import { IndicatorPane } from './IndicatorPane';
import { priceOverlays, separatePanes } from './indicatorSeries';
import { chartPalette } from './chartTheme';
import { useSettingsStore } from '@/stores/settingsStore';

interface Props {
  instrument: string;
  timeframe: Timeframe;
  onInstrument: (s: string) => void;
  onTimeframe: (t: Timeframe) => void;
  height?: string;
  /** Fill the parent's height (the chart grows to whatever space the container gives it). */
  fill?: boolean;
  /** Extra toolbar controls (e.g. an Overlays button). */
  toolbarExtra?: ReactNode;
  className?: string;
}

/** Inferred price precision: backend-provided via specs later; demo profile for now. */
function precisionFor(symbol: string, sample?: number): number {
  if (sample !== undefined) {
    const s = sample.toString();
    const d = s.includes('.') ? s.split('.')[1]!.length : 0;
    return Math.max(d, demoProfile(symbol).digits);
  }
  return demoProfile(symbol).digits;
}

export function ChartWorkspace({ instrument, timeframe, onInstrument, onTimeframe, height = 'h-[480px]', fill = false, toolbarExtra, className }: Props) {
  const chartRef = useRef<CandlestickChartHandle>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const instruments = useInstruments();
  const candles = useCandles(instrument, timeframe);
  const overlaysQ = useOverlays(instrument, timeframe);
  const { indicators, overlays: overlayToggles, showVolume } = useChartStore();

  const data = useMemo(() => candles.data?.data ?? [], [candles.data]);
  const theme = useSettingsStore((s) => s.theme);
  const lines = useMemo(() => priceOverlays(data, indicators, chartPalette(theme)), [data, indicators, theme]);
  const panes = useMemo(() => separatePanes(data, indicators, chartPalette(theme)), [data, indicators, theme]);
  const visibleOverlays = useMemo(
    () => (overlaysQ.data?.data ?? []).filter((o) => overlayToggles[o.kind]),
    [overlaysQ.data, overlayToggles],
  );
  const subscribeRange = useCallback(
    (cb: Parameters<CandlestickChartHandle['subscribeRange']>[0]) => chartRef.current?.subscribeRange(cb) ?? (() => undefined),
    [],
  );

  const fullscreen = () => {
    const el = wrapRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else if (el.requestFullscreen) void el.requestFullscreen().catch(() => toast({ tone: 'warning', title: 'Fullscreen unavailable' }));
  };

  // Browser fullscreen always switches to fill mode so the chart uses the whole monitor.
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const onChange = () => setIsFullscreen(document.fullscreenElement === wrapRef.current && !!wrapRef.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);
  const filling = fill || isFullscreen;
  const area = filling ? 'min-h-[260px] flex-1' : height;

  const digits = precisionFor(instrument, data[data.length - 1]?.close);
  const isDemo = candles.data?.source === 'demo';

  return (
    <Card className={cn('overflow-hidden', fill && 'flex flex-col', className)}>
      <div ref={wrapRef} className={cn('flex flex-col bg-base-card', filling && 'min-h-0 flex-1', isFullscreen && 'h-screen')}>
        <ChartToolbar
          instruments={instruments.data?.data ?? DEFAULT_INSTRUMENTS}
          instrument={instrument}
          timeframe={timeframe}
          onInstrument={onInstrument}
          onTimeframe={onTimeframe}
          refreshing={candles.isFetching}
          onRefresh={() => void candles.refetch()}
          onReset={() => chartRef.current?.reset()}
          onExport={() => chartRef.current?.exportPng(`${instrument}_${timeframe}${isDemo ? '_DEMO' : ''}.png`)}
          onFullscreen={fullscreen}
          isFullscreen={isFullscreen}
          extra={toolbarExtra}
        />
        <div className="flex items-center justify-between gap-2 px-3 py-1.5 text-2xs text-neutral">
          <span className="num">
            {instrument} · {timeframe} · {data.length} bars · UTC
          </span>
          <DataSourceBadge source={candles.data?.source} fetchedAt={candles.data?.fetchedAt} />
        </div>
        {candles.isPending ? (
          <Skeleton className={cn(area, 'm-3 rounded')} />
        ) : candles.isError ? (
          <div className={area}>
            <ErrorState error={candles.error} onRetry={() => void candles.refetch()} />
          </div>
        ) : data.length === 0 ? (
          <div className={area}>
            <EmptyState title="No candles stored" description="Run a collection from the Data Centre to populate this instrument and timeframe." />
          </div>
        ) : (
          <>
            <CandlestickChart
              ref={chartRef}
              candles={data}
              digits={digits}
              lines={lines}
              overlays={visibleOverlays}
              showVolume={showVolume}
              watermark={isDemo ? 'Demo data' : undefined}
              className={cn('relative w-full', area)}
            />
            {panes.map((p) => (
              <IndicatorPane key={p.id} pane={p} candles={data} subscribeRange={subscribeRange} />
            ))}
          </>
        )}
      </div>
    </Card>
  );
}
