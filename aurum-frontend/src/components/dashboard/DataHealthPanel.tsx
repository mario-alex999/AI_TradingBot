import { Database } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { StatusIndicator } from '@/components/common/StatusIndicator';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Badge } from '@/components/common/Badge';
import { Skeleton } from '@/components/common/Skeleton';
import { describeError } from '@/utils/errors';
import { useDataQuality, useHealth } from '@/hooks/queries';
import { formatDateTime, formatInteger, PLACEHOLDER } from '@/utils/format';
import type { ConnectionState } from '@/types/system';

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line-soft py-2 last:border-0">
      <dt className="text-xs text-neutral">{label}</dt>
      <dd className="num text-right text-xs text-ink">{children}</dd>
    </div>
  );
}

export function DataHealthPanel() {
  const health = useHealth();
  const dq = useDataQuality();
  const conn = (s: ConnectionState | undefined): ConnectionState => (health.isError ? 'disconnected' : (s ?? 'unknown'));
  const q = dq.data?.data;
  const val = (v: number | null | undefined) => (dq.isPending ? <Skeleton className="ml-auto h-3 w-12" /> : formatInteger(v));

  return (
    <Card>
      <CardHeader
        icon={<Database className="h-4 w-4" />}
        title="Market data health"
        subtitle={health.isError ? describeError(health.error).title : 'Module 01 · MT5 data pipeline'}
        actions={
          <>
            <DataSourceBadge source={dq.data?.source} />
            <Link to="/data" className="text-xs font-medium text-ink underline decoration-line-control underline-offset-4 hover:decoration-ink">
              Data Centre
            </Link>
          </>
        }
      />
      <CardBody>
        <dl>
          <Row label="MT5 connection">{health.isPending ? <Skeleton className="ml-auto h-3 w-20" /> : <StatusIndicator status={conn(health.data?.mt5)} />}</Row>
          <Row label="Database connection">{health.isPending ? <Skeleton className="ml-auto h-3 w-20" /> : <StatusIndicator status={conn(health.data?.database)} />}</Row>
          <Row label="Last successful collection">{q ? formatDateTime(q.lastSuccessfulCollection) : PLACEHOLDER}</Row>
          <Row label="Instruments monitored">{q ? q.instrumentsMonitored.join(', ') : PLACEHOLDER}</Row>
          <Row label="Timeframes">
            {q ? (
              <span className="flex flex-wrap justify-end gap-1">
                {q.timeframes.map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
              </span>
            ) : (
              PLACEHOLDER
            )}
          </Row>
          <Row label="Candles collected">{val(q?.candlesCollected)}</Row>
          <Row label="Validation errors">{val(q?.validationErrors)}</Row>
          <Row label="Missing candles">{val(q?.missingCandles)}</Row>
          <Row label="Collection latency">{q?.latencyMs != null ? `${q.latencyMs} ms` : PLACEHOLDER}</Row>
        </dl>
        {dq.isError && <p className="mt-2 text-xs text-neutral">{describeError(dq.error).description}</p>}
      </CardBody>
    </Card>
  );
}
