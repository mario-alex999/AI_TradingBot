import { ShieldAlert } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { useDataQuality } from '@/hooks/queries';
import { scrollToLogs, useDataCentreStore } from '@/stores/dataCentreStore';
import { formatInteger } from '@/utils/format';
import { cn } from '@/utils/cn';

export function DataQualityPanel() {
  const dq = useDataQuality();
  const setStatus = useDataCentreStore((st) => st.setStatus);
  return (
    <Card>
      <CardHeader icon={<ShieldAlert className="h-4 w-4" />} title="Data quality" actions={<DataSourceBadge source={dq.data?.source} />} />
      <CardBody>
        <QueryState query={dq}>
          {({ data: q }) => {
            // Each tile drills into the collection logs with the matching validation status.
            const tiles: [string, number | null, 'warning' | 'failed'][] = [
              ['Missing candles', q.missingCandles, 'warning'],
              ['Duplicate candles', q.duplicateCandles, 'warning'],
              ['Invalid OHLC', q.invalidOhlc, 'failed'],
              ['Unexpected gaps', q.unexpectedGaps, 'warning'],
              ['Collection errors', q.collectionErrors, 'failed'],
            ];
            const result = q.lastValidationResult;
            return (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {tiles.map(([label, v, st]) => (
                  <button
                    type="button"
                    key={label}
                    onClick={() => {
                      setStatus(st);
                      scrollToLogs();
                    }}
                    title={`Show ${st} runs in the collection logs`}
                    className="rounded-xl bg-base-raised p-3 text-left transition hover:ring-1 hover:ring-gold/40 active:scale-[0.98]"
                  >
                    <p className="eyebrow">{label}</p>
                    <p className={cn('num mt-1 text-lg font-semibold', v ? 'text-warn' : 'text-ink')}>{formatInteger(v)}</p>
                  </button>
                ))}
                <div className="rounded-md border border-line-soft bg-base-raised p-3">
                  <p className="eyebrow">Last validation</p>
                  <div className="mt-2">
                    {result ? <Badge tone={result === 'passed' ? 'up' : result === 'warning' ? 'warn' : 'down'}>{result}</Badge> : <span className="text-neutral">—</span>}
                  </div>
                </div>
              </div>
            );
          }}
        </QueryState>
      </CardBody>
    </Card>
  );
}
