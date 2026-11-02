import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { PriceChange } from '@/components/common/PriceChange';
import { FeaturePending } from '@/components/common/FeaturePending';
import { EmptyState } from '@/components/common/EmptyState';
import { ChartWorkspace } from '@/components/charts/ChartWorkspace';
import { useInstruments, useQuotes, useSpecifications } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { ASSET_CLASS_LABEL } from '@/config/instruments';
import { formatNumber, formatPrice, PLACEHOLDER } from '@/utils/format';
import type { Timeframe } from '@/types/market';

export default function InstrumentDetailPage() {
  const { symbol = '' } = useParams();
  const defaultTf = useSettingsStore((s) => s.defaultTimeframe);
  const [timeframe, setTimeframe] = useState<Timeframe>(defaultTf);
  const instruments = useInstruments();
  const quotes = useQuotes([symbol]);
  const specs = useSpecifications();
  const inst = instruments.data?.data.find((i) => i.symbol === symbol);
  const q = quotes.data?.data[0];
  const spec = specs.data?.data.find((s) => s.symbol === symbol);

  if (instruments.isSuccess && !inst)
    return <EmptyState title={`Unknown instrument "${symbol}"`} description="This symbol is not configured on the backend." action={<Link className="text-sm text-gold hover:underline" to="/market">Back to Market Overview</Link>} />;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Link to="/market" className="inline-flex items-center gap-1 text-xs text-neutral hover:text-ink">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Market Overview
        </Link>
        <div className="mt-2 flex flex-wrap items-end gap-x-6 gap-y-2">
          <div>
            <h1 className="num text-2xl font-semibold text-ink">{symbol}</h1>
            <p className="text-sm text-ink-muted">
              {inst?.name ?? PLACEHOLDER} · {inst ? ASSET_CLASS_LABEL[inst.assetClass] : PLACEHOLDER}
            </p>
          </div>
          {q && (
            <div className="flex items-end gap-4">
              <span className="num text-2xl text-ink">{formatPrice(q.bid, q.digits)}</span>
              <PriceChange value={q.dailyChangePct} className="pb-1" />
            </div>
          )}
          <DataSourceBadge source={quotes.data?.source} fetchedAt={quotes.data?.fetchedAt} />
        </div>
      </div>
      <ChartWorkspace instrument={symbol} timeframe={timeframe} onInstrument={() => undefined} onTimeframe={setTimeframe} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="Contract specification" actions={<DataSourceBadge source={specs.data?.source} />} />
          <CardBody>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
              {[
                ['Broker symbol', spec?.brokerSymbol ?? PLACEHOLDER],
                ['Digits', spec?.digits ?? PLACEHOLDER],
                ['Point', spec?.point != null ? spec.point.toString() : PLACEHOLDER],
                ['Contract size', formatNumber(spec?.contractSize, 0)],
                ['Tick size', spec?.tickSize != null ? spec.tickSize.toString() : PLACEHOLDER],
                ['Tick value', formatNumber(spec?.tickValue, 2)],
              ].map(([k, v]) => (
                <div key={String(k)} className="flex justify-between border-b border-line-soft py-1.5">
                  <dt className="text-neutral">{k}</dt>
                  <dd className="num text-ink">{v}</dd>
                </div>
              ))}
            </dl>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Structure & intelligence" />
          <CardBody className="grid gap-3 sm:grid-cols-2">
            <FeaturePending title="Market structure" requires="Market Structure Agent" />
            <FeaturePending title="COT positioning" requires="COT Intelligence Agent" />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
