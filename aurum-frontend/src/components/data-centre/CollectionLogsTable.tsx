import { useEffect, useState } from 'react';
import { ScrollText } from 'lucide-react';
import { Card, CardHeader } from '@/components/common/Card';
import { DataTable, type Column } from '@/components/common/DataTable';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Badge } from '@/components/common/Badge';
import { SearchInput } from '@/components/common/SearchInput';
import { Select } from '@/components/common/Select';
import { Pagination } from '@/components/common/Pagination';
import { QueryState } from '@/components/common/QueryState';
import { EmptyState } from '@/components/common/EmptyState';
import { useCollectionLogs } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import { formatDateTime, formatInteger } from '@/utils/format';
import type { CollectionLog, ValidationStatus } from '@/types/system';
import type { Timeframe } from '@/types/market';

const PAGE_SIZE = 12;

export function CollectionLogsTable() {
  const [search, setSearch] = useState('');
  const [debounced, setDebounced] = useState('');
  const [instrument, setInstrument] = useState('');
  const [timeframe, setTimeframe] = useState<Timeframe | ''>('');
  const [status, setStatus] = useState<ValidationStatus | ''>('');
  const [page, setPage] = useState(1);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(t);
  }, [search]);
  useEffect(() => setPage(1), [debounced, instrument, timeframe, status]);

  const logs = useCollectionLogs({ page, pageSize: PAGE_SIZE, search: debounced, instrument, timeframe, status });

  const columns: Column<CollectionLog>[] = [
    { key: 'ts', header: 'Timestamp (UTC)', cell: (r) => <span className="num text-xs text-ink-muted">{formatDateTime(r.timestamp)}</span> },
    { key: 'inst', header: 'Instrument', cell: (r) => <span className="num font-medium text-ink">{r.instrument}</span> },
    { key: 'tf', header: 'Timeframe', cell: (r) => <span className="num">{r.timeframe}</span> },
    { key: 'ret', header: 'Retrieved', align: 'right', cell: (r) => <span className="num">{formatInteger(r.recordsRetrieved)}</span> },
    { key: 'sto', header: 'Stored', align: 'right', cell: (r) => <span className="num">{formatInteger(r.recordsStored)}</span> },
    {
      key: 'val',
      header: 'Validation',
      cell: (r) => <Badge tone={r.validationStatus === 'passed' ? 'up' : r.validationStatus === 'warning' ? 'warn' : 'down'}>{r.validationStatus}</Badge>,
    },
    { key: 'dur', header: 'Duration', align: 'right', cell: (r) => <span className="num text-ink-muted">{r.durationMs} ms</span> },
    { key: 'err', header: 'Error', cell: (r) => <span className="text-xs text-neutral">{r.errorMessage ?? '—'}</span> },
  ];

  return (
    <Card>
      <CardHeader icon={<ScrollText className="h-4 w-4" />} title="Collection logs" actions={<DataSourceBadge source={logs.data?.source} fetchedAt={logs.data?.fetchedAt} />} />
      <div className="grid gap-3 border-b border-line p-3 md:grid-cols-[minmax(0,1fr)_150px_130px_150px]">
        <SearchInput label="Search logs" value={search} onChange={setSearch} placeholder="Search instrument, timeframe or error" />
        <Select label="Instrument filter" hideLabel value={instrument} onChange={setInstrument} options={[{ value: '', label: 'All instruments' }, ...DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))]} />
        <Select label="Timeframe filter" hideLabel value={timeframe} onChange={(v) => setTimeframe(v as Timeframe | '')} options={[{ value: '', label: 'All timeframes' }, ...TIMEFRAMES.map((t) => ({ value: t, label: t }))]} />
        <Select
          label="Validation filter"
          hideLabel
          value={status}
          onChange={(v) => setStatus(v as ValidationStatus | '')}
          options={[
            { value: '', label: 'Any validation' },
            { value: 'passed', label: 'Passed' },
            { value: 'warning', label: 'Warning' },
            { value: 'failed', label: 'Failed' },
          ]}
        />
      </div>
      <QueryState query={logs}>
        {(d) => (
          <>
            <DataTable
              caption="Collection logs"
              dense
              columns={columns}
              rows={d.data.items}
              rowKey={(r) => r.id}
              empty={<EmptyState compact title="No collection runs recorded" description="Logs appear after the backend completes a collection." />}
            />
            <div className="border-t border-line px-3 py-2">
              <Pagination page={d.data.page} pageSize={d.data.pageSize} total={d.data.total} onPage={setPage} />
            </div>
          </>
        )}
      </QueryState>
    </Card>
  );
}
