import { RefreshCw } from 'lucide-react';
import { QuoteCard } from '@/components/market/QuoteCard';
import { Button } from '@/components/common/Button';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { Skeleton } from '@/components/common/Skeleton';
import { Card } from '@/components/common/Card';
import { useInstruments, useQuotes } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';

const SYMBOLS = DEFAULT_INSTRUMENTS.map((i) => i.symbol);

export function MarketCards() {
  const quotes = useQuotes(SYMBOLS);
  const instruments = useInstruments();
  const bySymbol = new Map((instruments.data?.data ?? DEFAULT_INSTRUMENTS).map((i) => [i.symbol, i]));
  return (
    <section aria-labelledby="markets-title" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 id="markets-title" className="text-sm font-semibold text-ink">
            Market overview
          </h2>
          <DataSourceBadge source={quotes.data?.source} fetchedAt={quotes.data?.fetchedAt} />
        </div>
        <Button
          size="sm"
          variant="ghost"
          icon={<RefreshCw className={quotes.isFetching ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} aria-hidden />}
          onClick={() => void quotes.refetch()}
        >
          Refresh
        </Button>
      </div>
      {quotes.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {SYMBOLS.map((s) => (
            <Skeleton key={s} className="h-[212px] rounded-card" />
          ))}
        </div>
      ) : quotes.isError ? (
        <Card>
          <ErrorState error={quotes.error} onRetry={() => void quotes.refetch()} compact />
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {quotes.data.data.map((q) => (
            <QuoteCard key={q.symbol} quote={q} instrument={bySymbol.get(q.symbol)} source={quotes.data.source} />
          ))}
        </div>
      )}
    </section>
  );
}
