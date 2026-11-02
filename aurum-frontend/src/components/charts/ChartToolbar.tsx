import { Download, Maximize2, PenTool, RefreshCw, RotateCcw, Settings2, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { Select } from '@/components/common/Select';
import { Popover } from '@/components/common/Popover';
import { Switch } from '@/components/common/Switch';
import { TIMEFRAMES } from '@/config/instruments';
import { useChartStore, type IndicatorId } from '@/stores/chartStore';
import type { Instrument, Timeframe } from '@/types/market';
import { INDICATOR_META } from './indicatorSeries';

interface Props {
  instruments: Instrument[];
  instrument: string;
  timeframe: Timeframe;
  onInstrument: (s: string) => void;
  onTimeframe: (t: Timeframe) => void;
  onRefresh: () => void;
  onExport: () => void;
  onReset: () => void;
  onFullscreen: () => void;
  refreshing?: boolean;
}

export function ChartToolbar(p: Props) {
  const { indicators, toggleIndicator, setIndicatorPeriod, showVolume, setShowVolume } = useChartStore();
  const enabledCount = Object.values(indicators).filter((i) => i.enabled).length;
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2" role="toolbar" aria-label="Chart toolbar">
      <Select
        label="Instrument"
        hideLabel
        value={p.instrument}
        onChange={p.onInstrument}
        options={p.instruments.map((i) => ({ value: i.symbol, label: i.symbol }))}
        className="w-32"
      />
      <SegmentedControl label="Timeframe" value={p.timeframe} options={TIMEFRAMES} onChange={p.onTimeframe} />
      <span className="mx-1 hidden h-5 w-px bg-line sm:block" aria-hidden />
      <Popover
        trigger={(t) => (
          <Button size="sm" variant="ghost" icon={<SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />} {...t}>
            Indicators{enabledCount ? ` (${enabledCount})` : ''}
          </Button>
        )}
      >
        <p className="eyebrow mb-2">Technical indicators</p>
        <div className="flex flex-col gap-2.5">
          {(Object.keys(INDICATOR_META) as IndicatorId[]).map((id) => (
            <div key={id} className="flex items-center gap-2">
              <div className="flex-1">
                <Switch label={INDICATOR_META[id].label} checked={indicators[id].enabled} onChange={() => toggleIndicator(id)} />
              </div>
              {INDICATOR_META[id].hasPeriod && (
                <>
                  <label className="sr-only" htmlFor={`period-${id}`}>{`${INDICATOR_META[id].label} period`}</label>
                  <input
                    id={`period-${id}`}
                    type="number"
                    min={2}
                    max={400}
                    value={indicators[id].period ?? ''}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (v >= 2 && v <= 400) setIndicatorPeriod(id, v);
                    }}
                    className="field num h-7 w-16 px-2 text-xs"
                  />
                </>
              )}
            </div>
          ))}
        </div>
      </Popover>
      <Button
        size="sm"
        variant="ghost"
        disabled
        disabledReason="Drawing tools require backend persistence of annotations (planned)"
        icon={<PenTool className="h-3.5 w-3.5" aria-hidden />}
      >
        Draw
      </Button>
      <Popover
        trigger={(t) => (
          <Button size="sm" variant="ghost" icon={<Settings2 className="h-3.5 w-3.5" aria-hidden />} {...t}>
            Settings
          </Button>
        )}
      >
        <p className="eyebrow mb-2">Chart settings</p>
        <Switch label="Volume histogram" checked={showVolume} onChange={setShowVolume} />
      </Popover>
      <div className="ml-auto flex items-center gap-1">
        <Button size="icon" variant="ghost" aria-label="Refresh data" onClick={p.onRefresh}>
          <RefreshCw className={p.refreshing ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
        </Button>
        <Button size="icon" variant="ghost" aria-label="Reset chart view" onClick={p.onReset}>
          <RotateCcw className="h-4 w-4" />
        </Button>
        <Button size="icon" variant="ghost" aria-label="Export chart as PNG" onClick={p.onExport}>
          <Download className="h-4 w-4" />
        </Button>
        <Button size="icon" variant="ghost" aria-label="Fullscreen" onClick={p.onFullscreen}>
          <Maximize2 className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
