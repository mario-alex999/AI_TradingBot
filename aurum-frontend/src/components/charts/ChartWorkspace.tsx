import { useCallback, useMemo, useRef } from 'react';
import { Card } from '@/components/common/Card';
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

interface Props {
  instrument: string;
  timeframe: Timeframe;
  onInstrument: (s: string) => void;
  onTimeframe: (t: Timeframe) => void;
  height?: string;
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

export function ChartWorkspace({ instrument, timeframe, onInstrument, onTimeframe, height = 'h-[480px]' }: Props) {
  const chartRef = useRef<CandlestickChartHandle>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const instruments = useInstruments();
  const candles = useCandles(instrument, timeframe);
  const overlaysQ = useOverlays(instrument, timeframe);
  const { indicators, overlays: overlayToggles, showVolume } = useChartStore();

  const data = useMemo(() => candles.data?.data ?? [], [candles.data]);
  const lines = useMemo(() => priceOverlays(data, indicators), [data, indicators]);
  const panes = useMemo(() => separatePanes(data, indicators), [data, indicators]);
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

  const digits = precisionFor(instrument, data[data.length - 1]?.close);
  const isDemo = candles.data?.source === 'demo';

  return (
    <Card className="overflow-hidden">
      <div ref={wrapRef} className="flex flex-col bg-base-card">
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
        />
        <div className="flex items-center justify-between gap-2 px-3 py-1.5 text-2xs text-neutral">
          <span className="num">
            {instrument} · {timeframe} · {data.length} bars · UTC
          </span>
          <DataSourceBadge source={candles.data?.source} fetchedAt={candles.data?.fetchedAt} />
        </div>
        {candles.isPending ? (
          <Skeleton className={`${height} m-3 rounded`} />
        ) : candles.isError ? (
          <div className={height}>
            <ErrorState error={candles.error} onRetry={() => void candles.refetch()} />
          </div>
        ) : data.length === 0 ? (
          <div className={height}>
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
              className={`relative w-full ${height}`}
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
