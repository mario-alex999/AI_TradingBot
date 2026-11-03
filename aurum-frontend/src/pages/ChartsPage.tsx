import { useEffect, useState } from 'react';
import { Layers, X } from 'lucide-react';
import { ChartWorkspace } from '@/components/charts/ChartWorkspace';
import { OverlayPanel } from '@/components/charts/OverlayPanel';
import { Button } from '@/components/common/Button';
import { useChartSelection } from '@/hooks/useChartSelection';
import { useOverlays } from '@/hooks/queries';

/**
 * Full-screen charting workspace: the chart takes the whole width and the full
 * window height below the header; structure overlays open in a side panel.
 */
export default function ChartsPage() {
  const { instrument, timeframe, setInstrument, setTimeframe } = useChartSelection();
  const overlays = useOverlays(instrument, timeframe);
  const [panelOpen, setPanelOpen] = useState(false);
  const available = overlays.data?.data ?? [];

  useEffect(() => {
    if (!panelOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPanelOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [panelOpen]);

  return (
    <div className="flex h-[calc(100dvh-178px)] min-h-[520px] flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-[-0.01em] text-ink">Charts &amp; Analysis</h1>
        <p className="text-xs text-neutral">Drag to pan · scroll or pinch to zoom · double-click the price scale to auto-fit</p>
      </div>

      <ChartWorkspace
        fill
        className="min-h-0 flex-1"
        instrument={instrument}
        timeframe={timeframe}
        onInstrument={setInstrument}
        onTimeframe={setTimeframe}
        toolbarExtra={
          <Button
            size="sm"
            variant="ghost"
            icon={<Layers className="h-3.5 w-3.5" aria-hidden />}
            aria-expanded={panelOpen}
            aria-controls="overlay-panel"
            onClick={() => setPanelOpen((o) => !o)}
          >
            Overlays{available.length ? ` (${available.length})` : ''}
          </Button>
        }
      />

      {panelOpen && (
        <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="Structure overlays">
          <button type="button" aria-label="Close overlays" className="absolute inset-0 bg-black/20" onClick={() => setPanelOpen(false)} />
          <aside id="overlay-panel" className="absolute inset-y-0 right-0 flex w-[min(92vw,340px)] animate-fade-in flex-col bg-base-card shadow-float">
            <div className="flex items-center justify-end px-3 pt-3">
              <Button size="icon" variant="ghost" aria-label="Close overlays" onClick={() => setPanelOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 pb-6">
              <OverlayPanel available={available} />
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
