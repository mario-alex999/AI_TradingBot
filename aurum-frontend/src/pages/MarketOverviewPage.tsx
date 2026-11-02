import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { Card } from '@/components/common/Card';
import { PageHeader } from '@/components/common/PageHeader';
import { Button } from '@/components/common/Button';
import { SearchInput } from '@/components/common/SearchInput';
import { Select } from '@/components/common/Select';
import { DataTable, type Column } from '@/components/common/DataTable';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Badge } from '@/components/common/Badge';
import { PriceChange } from '@/components/common/PriceChange';
import { EmptyState } from '@/components/common/EmptyState';
import { ErrorState } from '@/components/common/ErrorState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { useInstruments, useQuotes } from '@/hooks/queries';
import { ASSET_CLASS_LABEL } from '@/config/instruments';
import { formatCompact, formatInZone, formatPrice } from '@/utils/format';
import type { AssetClass, Instrument, MarketStatus, Quote } from '@/types/market';

type Row = Quote & { instrument: Instrument | undefined };

export default function MarketOverviewPage() {
  const navigate = useNavigate();
  const instruments = useInstruments();
  const symbols = instruments.data?.data.filter((i) => i.enabled).map((i) => i.symbol);
  const quotes = useQuotes(symbols);
  const [search, setSearch] = useState('');
  const [assetClass, setAssetClass] = useState<AssetClass | ''>('');
  const [status, setStatus] = useState<MarketStatus | ''>('');

  const rows: Row[] = useMemo(() => {
    const map = new Map((instruments.data?.data ?? []).map((i) => [i.symbol, i]));
    const s = search.trim().toUpperCase();
    return (quotes.data?.data ?? [])
      .map((q) => ({ ...q, instrument: map.get(q.symbol) }))
      .filter(
        (r) =>
          (!s || r.symbol.includes(s) || (r.instrument?.name.toUpperCase().includes(s) ?? false)) &&
          (!assetClass || r.instrument?.assetClass === assetClass) &&
          (!status || r.status === status),
      );
  }, [quotes.data, instruments.data, search, assetClass, status]);

  const columns: Column<Row>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      sortValue: (r) => r.symbol,
      cell: (r) => (
        <div>
          <p className="num font-semibold text-ink">{r.symbol}</p>
          <p className="text-2xs text-neutral">{r.instrument?.name}</p>
        </div>
      ),
    },
    { key: 'class', header: 'Asset class', sortValue: (r) => r.instrument?.assetClass ?? '', cell: (r) => <span className="text-ink-muted">{r.instrument ? ASSET_CLASS_LABEL[r.instrument.assetClass] : '--'}</span> },
    { key: 'bid', header: 'Bid', align: 'right', sortValue: (r) => r.bid, cell: (r) => <span className="num text-ink">{formatPrice(r.bid, r.digits)}</span> },
    { key: 'ask', header: 'Ask', align: 'right', sortValue: (r) => r.ask, cell: (r) => <span className="num text-ink">{formatPrice(r.ask, r.digits)}</span> },
    { key: 'spread', header: 'Spread', align: 'right', sortValue: (r) => r.spread, cell: (r) => <span className="num text-ink-muted">{r.spread}</span> },
    { key: 'chg', header: 'Daily change', align: 'right', sortValue: (r) => r.dailyChangePct, cell: (r) => <PriceChange value={r.dailyChangePct} /> },
    { key: 'high', header: 'Daily high', align: 'right', sortValue: (r) => r.dailyHigh, cell: (r) => <span className="num text-ink-muted">{formatPrice(r.dailyHigh, r.digits)}</span> },
    { key: 'low', header: 'Daily low', align: 'right', sortValue: (r) => r.dailyLow, cell: (r) => <span className="num text-ink-muted">{formatPrice(r.dailyLow, r.digits)}</span> },
    { key: 'vol', header: 'Volume', align: 'right', sortValue: (r) => r.volume, cell: (r) => <span className="num text-ink-muted">{formatCompact(r.volume)}</span> },
    { key: 'upd', header: 'Last updated', align: 'right', sortValue: (r) => r.updatedAt, cell: (r) => <span className="num text-2xs text-neutral">{formatInZone(r.updatedAt, 'UTC')} UTC</span> },
    {
      key: 'status',
      header: 'Status',
      align: 'center',
      sortValue: (r) => r.status,
      cell: (r) =>
        quotes.data?.source === 'demo' ? (
          <Badge tone="gold">Demo</Badge>
        ) : (
          <Badge tone={r.status === 'open' ? 'up' : r.status === 'halted' ? 'down' : 'neutral'}>{r.status}</Badge>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Markets"
        title="Market Overview"
        description="All instruments configured on the backend. Select a row for detailed analysis."
        actions={
          <>
            <DataSourceBadge source={quotes.data?.source} fetchedAt={quotes.data?.fetchedAt} />
            <Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden />} onClick={() => void quotes.refetch()} loading={quotes.isFetching && !quotes.isPending}>
              Refresh
            </Button>
          </>
        }
      />
      <Card>
        <div className="grid gap-3 border-b border-line p-3 sm:grid-cols-[minmax(0,1fr)_180px_180px]">
          <SearchInput label="Search instruments" value={search} onChange={setSearch} placeholder="Search symbol or name" />
          <Select
            label="Asset class"
            hideLabel
            value={assetClass}
            onChange={(v) => setAssetClass(v as AssetClass | '')}
            options={[{ value: '', label: 'All asset classes' }, ...Object.entries(ASSET_CLASS_LABEL).map(([value, label]) => ({ value, label }))]}
          />
          <Select
            label="Market status"
            hideLabel
            value={status}
            onChange={(v) => setStatus(v as MarketStatus | '')}
            options={[
              { value: '', label: 'Any status' },
              { value: 'open', label: 'Open' },
              { value: 'closed', label: 'Closed' },
              { value: 'halted', label: 'Halted' },
              { value: 'unknown', label: 'Unknown' },
            ]}
          />
        </div>
        {quotes.isPending || instruments.isPending ? (
          <div className="p-4">
            <SkeletonRows rows={5} />
          </div>
        ) : quotes.isError ? (
          <ErrorState error={quotes.error} onRetry={() => void quotes.refetch()} />
        ) : (
          <DataTable
            caption="Market quotes"
            columns={columns}
            rows={rows}
            rowKey={(r) => r.symbol}
            rowLabel={(r) => `Open ${r.symbol} analysis`}
            onRowClick={(r) => navigate(`/market/${r.symbol}`)}
            initialSort={{ key: 'symbol', dir: 'asc' }}
            empty={<EmptyState compact title="No instruments match your filters" />}
          />
        )}
      </Card>
    </div>
  );
}
