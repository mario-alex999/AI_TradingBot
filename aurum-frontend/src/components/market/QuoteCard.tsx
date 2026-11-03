import { Link } from 'react-router-dom';
import { Card } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { PriceChange } from '@/components/common/PriceChange';
import { formatInZone, formatPrice } from '@/utils/format';
import type { Instrument, MarketStatus, Quote } from '@/types/market';
import type { DataSource } from '@/types/api';
import { ASSET_CLASS_LABEL } from '@/config/instruments';

const STATUS: Record<MarketStatus, { label: string; tone: 'up' | 'neutral' | 'down' }> = {
  open: { label: 'Open', tone: 'up' },
  closed: { label: 'Closed', tone: 'neutral' },
  halted: { label: 'Halted', tone: 'down' },
  unknown: { label: 'Status n/a', tone: 'neutral' },
};

export function QuoteCard({ quote, instrument, source }: { quote: Quote; instrument?: Instrument; source: DataSource }) {
  const range = quote.dailyHigh - quote.dailyLow;
  const pos = range > 0 ? Math.min(100, Math.max(0, ((quote.bid - quote.dailyLow) / range) * 100)) : 50;
  const st = STATUS[quote.status];
  return (
    <Card className="group relative flex flex-col gap-3 p-4 transition-colors hover:border-line-control">
      <div className="flex items-start justify-between gap-2">
        <div>
          <Link to={`/market/${quote.symbol}`} className="num text-base font-semibold text-ink after:absolute after:inset-0 hover:underline hover:decoration-line-control hover:underline-offset-4">
            {quote.symbol}
          </Link>
          <p className="text-xs text-neutral">{instrument ? ASSET_CLASS_LABEL[instrument.assetClass] : '—'}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {source === 'demo' ? <span className="text-xs text-neutral">Demo feed</span> : <Badge tone={st.tone}>{st.label}</Badge>}
          <PriceChange value={quote.dailyChangePct} className="text-xs" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <p className="eyebrow">Bid</p>
          {/* Prices stay in ink; only the daily change (the "what moved" figure) carries polarity. */}
          <p className="num text-xl text-ink">{formatPrice(quote.bid, quote.digits)}</p>
        </div>
        <div className="text-right">
          <p className="eyebrow">Ask</p>
          <p className="num text-xl text-ink-muted">{formatPrice(quote.ask, quote.digits)}</p>
        </div>
      </div>

      <div>
        <div className="relative h-1 rounded-full bg-base-elevated" aria-hidden>
          <span className="absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 rounded bg-gold" style={{ left: `${pos}%` }} />
        </div>
        <div className="num mt-1.5 flex justify-between text-xs text-neutral">
          <span>L {formatPrice(quote.dailyLow, quote.digits)}</span>
          <span>H {formatPrice(quote.dailyHigh, quote.digits)}</span>
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-line-soft pt-2 text-xs text-neutral">
        <span>
          Spread <span className="num text-ink-muted">{quote.spread}</span> pts
        </span>
        <span className="num" title={quote.updatedAt}>
          {formatInZone(quote.updatedAt, 'UTC')} UTC
        </span>
      </div>
    </Card>
  );
}
