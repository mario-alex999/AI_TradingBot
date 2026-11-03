import { cn } from '@/utils/cn';
import { Badge } from '@/components/common/Badge';
import type { CotInstrument } from '@/types/cot';
import { REPORT_TYPE_LABEL } from '@/types/cot';

/** B. Instrument selector with the verified futures mapping shown beside each instrument. */
export function CotInstrumentSelector({ instruments, value, onChange }: { instruments: CotInstrument[]; value: string; onChange: (s: string) => void }) {
  return (
    <div role="radiogroup" aria-label="Instrument" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {instruments.map((i) => {
        const m = i.mappings[0];
        const active = i.instrument === value;
        return (
          <button
            key={i.instrument}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(i.instrument)}
            className={cn(
              'flex flex-col gap-1 rounded-card border px-3 py-2.5 text-left transition-colors',
              active ? 'border-gold bg-base-card' : 'border-line bg-base-card hover:border-line-control',
            )}
          >
            <span className="flex items-center justify-between gap-2">
              <span className={cn('num text-sm font-semibold', 'text-ink')}>{i.instrument}</span>
              {m && <Badge tone={m.mappingStatus === 'verified' ? 'up' : 'warn'}>{m.mappingStatus}</Badge>}
            </span>
            {m && (
              <span className="text-2xs text-neutral">
                {m.cftcContractName} · {m.cftcContractCode} · {m.exchange}
              </span>
            )}
            <span className="text-2xs text-ink-muted">{i.mappings.map((x) => REPORT_TYPE_LABEL[x.reportType]).join(' · ')}</span>
          </button>
        );
      })}
    </div>
  );
}
