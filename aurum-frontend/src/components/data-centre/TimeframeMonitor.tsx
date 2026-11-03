import { Layers3 } from 'lucide-react';
import { Card, CardHeader } from '@/components/common/Card';
import { DataTable, type Column } from '@/components/common/DataTable';
import { StatusIndicator } from '@/components/common/StatusIndicator';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { useDataQuality } from '@/hooks/queries';
import { scrollToLogs, useDataCentreStore } from '@/stores/dataCentreStore';
import { formatDateTime, formatInteger } from '@/utils/format';
import type { TimeframeHealth } from '@/types/system';

const AVAIL: Record<TimeframeHealth['availability'], 'up' | 'warn' | 'down' | 'neutral'> = {
  available: 'up',
  partial: 'warn',
  missing: 'down',
  unknown: 'neutral',
};

export function TimeframeMonitor() {
  const dq = useDataQuality();
  const setTimeframe = useDataCentreStore((st) => st.setTimeframe);
  const columns: Column<TimeframeHealth>[] = [
    { key: 'tf', header: 'Timeframe', cell: (r) => <span className="num font-semibold text-ink">{r.timeframe}</span> },
    { key: 'latest', header: 'Latest candle', cell: (r) => <span className="num text-xs text-ink-muted">{formatDateTime(r.latestCandle)}</span> },
    { key: 'stored', header: 'Stored candles', align: 'right', sortValue: (r) => r.storedCandles, cell: (r) => <span className="num">{formatInteger(r.storedCandles)}</span> },
    { key: 'last', header: 'Last collection', cell: (r) => <span className="num text-xs text-ink-muted">{formatDateTime(r.lastCollection)}</span> },
    { key: 'avail', header: 'Availability', cell: (r) => <Badge tone={AVAIL[r.availability]}>{r.availability}</Badge> },
    { key: 'q', header: 'Quality', cell: (r) => <StatusIndicator status={r.quality} /> },
  ];
  return (
    <Card>
      <CardHeader icon={<Layers3 className="h-4 w-4" />} title="Timeframe monitoring" actions={<DataSourceBadge source={dq.data?.source} />} />
      <QueryState query={dq}>{(d) => <DataTable
            caption="Timeframe health"
            dense
            columns={columns}
            rows={d.data.byTimeframe}
            rowKey={(r) => r.timeframe}
            onRowClick={(r) => {
              setTimeframe(r.timeframe);
              scrollToLogs();
            }}
            rowLabel={(r) => `Show ${r.timeframe} collection logs`}
          />}</QueryState>
    </Card>
  );
}
