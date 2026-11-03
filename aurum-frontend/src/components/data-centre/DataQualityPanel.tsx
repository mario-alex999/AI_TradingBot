import { ShieldAlert } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { useDataQuality } from '@/hooks/queries';
import { formatInteger } from '@/utils/format';
import { cn } from '@/utils/cn';

export function DataQualityPanel() {
  const dq = useDataQuality();
  return (
    <Card>
      <CardHeader icon={<ShieldAlert className="h-4 w-4" />} title="Data quality" actions={<DataSourceBadge source={dq.data?.source} />} />
      <CardBody>
        <QueryState query={dq}>
          {({ data: q }) => {
            const tiles: [string, number | null][] = [
              ['Missing candles', q.missingCandles],
              ['Duplicate candles', q.duplicateCandles],
              ['Invalid OHLC', q.invalidOhlc],
              ['Unexpected gaps', q.unexpectedGaps],
              ['Collection errors', q.collectionErrors],
            ];
            const result = q.lastValidationResult;
            return (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {tiles.map(([label, v]) => (
                  <div key={label} className="rounded-md border border-line-soft bg-base-raised p-3">
                    <p className="eyebrow">{label}</p>
                    <p className={cn('num mt-1 text-lg font-semibold', v ? 'text-warn' : 'text-ink')}>{formatInteger(v)}</p>
                  </div>
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
