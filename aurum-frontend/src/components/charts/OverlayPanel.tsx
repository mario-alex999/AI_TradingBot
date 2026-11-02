import { Layers } from 'lucide-react';
import { Switch } from '@/components/common/Switch';
import { useChartStore } from '@/stores/chartStore';
import { OVERLAY_LABEL } from '@/config/overlays';
import type { ChartOverlay, OverlayKind } from '@/types/market';


/**
 * Overlay toggles. A toggle is enabled only when the analysis backend has
 * supplied overlays of that kind — nothing is derived in the browser.
 */
export function OverlayPanel({ available }: { available: ChartOverlay[] }) {
  const { overlays, toggleOverlay } = useChartStore();
  const counts = available.reduce<Partial<Record<OverlayKind, number>>>((m, o) => ({ ...m, [o.kind]: (m[o.kind] ?? 0) + 1 }), {});
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 text-gold" aria-hidden />
        <h3 className="text-sm font-semibold text-ink">Structure overlays</h3>
      </div>
      <p className="text-2xs leading-relaxed text-neutral">Supplied by the market analysis backend. AURUM never generates SMC/ICT levels in the browser.</p>
      <div className="flex flex-col gap-2.5">
        {(Object.keys(OVERLAY_LABEL) as OverlayKind[]).map((k) => {
          const n = counts[k] ?? 0;
          return (
            <Switch
              key={k}
              label={OVERLAY_LABEL[k]}
              description={n ? `${n} from backend` : 'Awaiting analysis backend'}
              checked={!!overlays[k] && n > 0}
              disabled={n === 0}
              disabledReason="No overlays of this type have been supplied by the backend"
              onChange={() => toggleOverlay(k)}
            />
          );
        })}
      </div>
    </div>
  );
}
