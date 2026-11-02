import { useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { exportCot } from '@/services/cotService';
import { saveBlob } from '@/utils/download';
import { toApiError } from '@/services/api';
import type { CotExportKind } from '@/types/cot';

const KINDS: { kind: CotExportKind; label: string }[] = [
  { kind: 'analysis', label: 'Analysis' },
  { kind: 'metrics', label: 'Metrics' },
  { kind: 'positions', label: 'Positions' },
  { kind: 'raw', label: 'Raw CFTC' },
  { kind: 'quality', label: 'Quality' },
];

/** CSV exports from the backend (every row is marked real / incomplete / unavailable). Disabled in demo mode. */
export function CotExportButtons({ instrument, demo }: { instrument: string; demo: boolean }) {
  const [busy, setBusy] = useState<CotExportKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (kind: CotExportKind) => {
    setBusy(kind);
    setError(null);
    try {
      saveBlob(await exportCot(kind, kind === 'quality' ? undefined : instrument), `cot_${kind}_${instrument}.csv`);
    } catch (e) {
      setError(toApiError(e).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="CSV exports">
      {KINDS.map((k) => (
        <Button key={k.kind} size="sm" variant="ghost" disabled={demo || busy !== null} disabledReason={demo ? 'Exports contain backend data only' : undefined} onClick={() => void run(k.kind)}>
          <Download className="h-3.5 w-3.5" aria-hidden /> {busy === k.kind ? 'Exporting…' : k.label}
        </Button>
      ))}
      {error && <span className="text-2xs text-down-text">{error}</span>}
    </div>
  );
}
