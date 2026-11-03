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
import { useDataCentreStore } from '@/stores/dataCentreStore';
import { Drawer } from '@/components/common/Drawer';
import { Button } from '@/components/common/Button';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import { formatDateTime, formatInteger } from '@/utils/format';
import type { CollectionLog, ValidationStatus } from '@/types/system';
import type { Timeframe } from '@/types/market';

const PAGE_SIZE = 12;

export function CollectionLogsTable() {
  const [search, setSearch] = useState('');
  const [debounced, setDebounced] = useState('');
  const [instrument, setInstrument] = useState('');
  const { timeframe, setTimeframe, status, setStatus } = useDataCentreStore();
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<CollectionLog | null>(null);

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
    <Card id="collection-logs" className="scroll-mt-28">
      <CardHeader icon={<ScrollText className="h-4 w-4" />} title="Collection logs" actions={<DataSourceBadge source={logs.data?.source} fetchedAt={logs.data?.fetchedAt} />} />
      <div className="grid gap-3 border-b border-line p-3 md:grid-cols-[minmax(0,1fr)_180px_170px_180px]">
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
              onRowClick={setOpen}
              rowLabel={(r) => `Open log for ${r.instrument} ${r.timeframe} at ${formatDateTime(r.timestamp)}`}
              empty={<EmptyState compact title="No collection runs recorded" description="Logs appear after the backend completes a collection." />}
            />
            <div className="border-t border-line px-3 py-2">
              <Pagination page={d.data.page} pageSize={d.data.pageSize} total={d.data.total} onPage={setPage} />
            </div>
          </>
        )}
      </QueryState>
      {(timeframe || status) && (
        <div className="flex items-center justify-between gap-3 border-t border-line-soft px-4 py-2 text-xs text-neutral">
          <span>
            Filtered{timeframe ? ` to ${timeframe}` : ''}
            {status ? ` · ${status}` : ''}
          </span>
          <Button size="sm" variant="ghost" onClick={() => { setTimeframe(''); setStatus(''); }}>
            Clear filters
          </Button>
        </div>
      )}
      <Drawer
        open={open !== null}
        onClose={() => setOpen(null)}
        title={open ? `${open.instrument} · ${open.timeframe}` : ''}
        subtitle={open ? `Collection run at ${formatDateTime(open.timestamp)} UTC` : undefined}
      >
        {open && (
          <dl className="grid grid-cols-[140px_1fr] gap-y-2.5 text-sm">
            <dt className="text-neutral">Validation</dt>
            <dd>
              <Badge tone={open.validationStatus === 'passed' ? 'up' : open.validationStatus === 'warning' ? 'warn' : 'down'}>{open.validationStatus}</Badge>
            </dd>
            <dt className="text-neutral">Records retrieved</dt>
            <dd className="num text-ink">{formatInteger(open.recordsRetrieved)}</dd>
            <dt className="text-neutral">Records stored</dt>
            <dd className="num text-ink">{formatInteger(open.recordsStored)}</dd>
            <dt className="text-neutral">Not stored</dt>
            <dd className="num text-ink">{formatInteger(open.recordsRetrieved - open.recordsStored)}</dd>
            <dt className="text-neutral">Duration</dt>
            <dd className="num text-ink">{open.durationMs} ms</dd>
            <dt className="text-neutral">Run ID</dt>
            <dd className="font-mono text-xs text-ink">{open.id}</dd>
            <dt className="text-neutral">Error</dt>
            <dd className="text-ink">{open.errorMessage ?? 'None'}</dd>
          </dl>
        )}
      </Drawer>
    </Card>
  );
}
