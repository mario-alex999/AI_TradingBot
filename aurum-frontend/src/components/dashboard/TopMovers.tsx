import { ArrowDownUp } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { useQuotes } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, ASSET_CLASS_LABEL } from '@/config/instruments';
import { formatPercent, formatPrice } from '@/utils/format';
import { usePriceFlash } from '@/hooks/usePriceFlash';
import { cn } from '@/utils/cn';
import type { Quote } from '@/types/market';

const SYMBOLS = DEFAULT_INSTRUMENTS.map((i) => i.symbol);
const CLASS = new Map(DEFAULT_INSTRUMENTS.map((i) => [i.symbol, ASSET_CLASS_LABEL[i.assetClass]]));

function Row({ q }: { q: Quote }) {
  const flash = usePriceFlash(q.bid);
  const up = q.dailyChangePct >= 0;
  return (
    <li>
      <Link
        to={`/market/${q.symbol}`}
        className="flex items-center justify-between gap-3 border-b border-feature-line px-5 py-3.5 transition-colors last:border-0 hover:bg-white/[0.06] focus-visible:bg-white/[0.06]"
      >
        <div className="min-w-0">
          <p className="text-sm font-medium text-white">{q.symbol}</p>
          <p className="text-xs text-feature-muted">{CLASS.get(q.symbol) ?? 'Instrument'}</p>
        </div>
        <div className="text-right">
          <span
            className={cn(
              'num inline-block rounded-full px-2 py-0.5 text-2xs font-semibold',
              up ? 'bg-feature-tag text-feature' : 'bg-[#FFD9D6] text-[#C4242A]',
            )}
          >
            {formatPercent(q.dailyChangePct, 2, true)}
          </span>
          <p className={cn('num mt-0.5 rounded px-1 text-sm text-white transition-colors duration-700', flash && 'bg-white/15 duration-75')}>
            {formatPrice(q.bid, q.digits)}
          </p>
        </div>
      </Link>
    </li>
  );
}

/** Dark-green list of today's movers; click the header control to flip the order. */
export function TopMovers() {
  const q = useQuotes(SYMBOLS);
  const [biggestFirst, setBiggestFirst] = useState(true);
  const rows = [...(q.data?.data ?? [])].sort((a, b) =>
    biggestFirst ? Math.abs(b.dailyChangePct) - Math.abs(a.dailyChangePct) : Math.abs(a.dailyChangePct) - Math.abs(b.dailyChangePct),
  );
  return (
    <section aria-labelledby="movers-title" className="flex flex-col overflow-hidden rounded-card bg-feature text-white">
      <div className="flex items-center justify-between gap-3 border-b border-feature-line px-5 py-4">
        <div>
          <h2 id="movers-title" className="text-base font-semibold">
            Top movers today
          </h2>
          <p className="text-xs text-feature-muted">{q.data?.source === 'demo' ? 'Demo feed · ranked by daily move' : 'Ranked by daily move'}</p>
        </div>
        <button
          type="button"
          onClick={() => setBiggestFirst((v) => !v)}
          aria-label={biggestFirst ? 'Show smallest moves first' : 'Show biggest moves first'}
          title={biggestFirst ? 'Biggest first' : 'Smallest first'}
          className="flex h-8 w-8 items-center justify-center rounded-full text-white/80 transition hover:bg-white/10 hover:text-white active:scale-90"
        >
          <ArrowDownUp className="h-4 w-4" />
        </button>
      </div>
      {q.isPending ? (
        <p className="px-5 py-6 text-sm text-feature-muted">Loading quotes…</p>
      ) : q.isError ? (
        <p className="px-5 py-6 text-sm text-feature-muted">Quotes unavailable. The backend did not respond.</p>
      ) : (
        <ul>
          {rows.map((r) => (
            <Row key={r.symbol} q={r} />
          ))}
        </ul>
      )}
    </section>
  );
}
