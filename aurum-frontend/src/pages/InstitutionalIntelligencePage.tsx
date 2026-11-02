import { useState } from 'react';
import { Info } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { MetricCard } from '@/components/common/MetricCard';
import { FeaturePending } from '@/components/common/FeaturePending';
import { ErrorState } from '@/components/common/ErrorState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { CotControls } from '@/components/cot/CotControls';
import { GroupComparisonChart, OpenInterestChart, PercentileChart } from '@/components/cot/CotCharts';
import { useCotAnalysis, type CotPeriod } from '@/hooks/useCotAnalysis';
import { formatDateTime, formatInteger, formatPercent, PLACEHOLDER } from '@/utils/format';
import type { CotDerived } from '@/utils/cot';

function signed(v: number | null | undefined) {
  if (v == null) return PLACEHOLDER;
  return `${v > 0 ? '+' : ''}${formatInteger(v)}`;
}
const tone = (v: number | null | undefined) => (v == null ? 'muted' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat') as 'up' | 'down' | 'flat' | 'muted';

function GroupPanel({ title, net, long, short, change }: { title: string; net?: number; long?: number; short?: number; change?: number | null }) {
  return (
    <MetricCard
      label={title}
      value={signed(net)}
      tone={tone(net)}
      hint={
        <span className="num">
          L {formatInteger(long)} · S {formatInteger(short)} · Δ {signed(change ?? null)}
        </span>
      }
    />
  );
}

export default function InstitutionalIntelligencePage() {
  const [instrument, setInstrument] = useState('EURUSD');
  const [period, setPeriod] = useState<CotPeriod>('3y');
  const [custom, setCustom] = useState({ from: '', to: '' });
  const { query, report, rows, latest, source } = useCotAnalysis(instrument, period, custom);
  const l: CotDerived | null = latest;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="Institutional Intelligence"
        description="Positioning of commercial hedgers, large speculators and small traders, derived from CFTC Commitments of Traders reports."
        actions={<DataSourceBadge source={source} fetchedAt={query.data?.fetchedAt} />}
      />
      <div className="flex gap-2 rounded-card border border-info/30 bg-info-faint px-4 py-3 text-xs text-ink-muted">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
        <p>
          COT reports show <strong className="text-ink">open positions</strong>, not realised profit or loss. AURUM AI does not infer the trading performance of
          commercial or institutional participants from these reports.
        </p>
      </div>
      <CotControls instrument={instrument} onInstrument={setInstrument} period={period} onPeriod={setPeriod} custom={custom} onCustom={setCustom} />

      {query.isPending ? (
        <SkeletonRows rows={6} />
      ) : query.isError ? (
        <Card>
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </Card>
      ) : (
        <>
          <Card>
            <CardBody className="num grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
              <div>
                <p className="eyebrow">Report date</p>
                <p className="mt-1 text-ink">{l?.reportDate ?? PLACEHOLDER}</p>
              </div>
              <div>
                <p className="eyebrow">Publication date</p>
                <p className="mt-1 text-ink">{l?.publicationDate ?? PLACEHOLDER}</p>
              </div>
              <div>
                <p className="eyebrow">Data source</p>
                <p className="mt-1 text-ink">{report?.source ?? PLACEHOLDER}</p>
              </div>
              <div>
                <p className="eyebrow">Updated</p>
                <p className="mt-1 text-ink">{report ? `${formatDateTime(report.updatedAt)} UTC` : PLACEHOLDER}</p>
              </div>
            </CardBody>
          </Card>

          <section aria-label="Positioning panels" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <GroupPanel title="Commercial net" net={l?.commercialNet} long={l?.commercialLong} short={l?.commercialShort} change={l?.commercialNetChange} />
            <GroupPanel title="Large speculator net" net={l?.nonCommercialNet} long={l?.nonCommercialLong} short={l?.nonCommercialShort} change={l?.nonCommercialNetChange} />
            <GroupPanel title="Non-reportable net" net={l?.nonReportableNet} long={l?.nonReportableLong} short={l?.nonReportableShort} />
            <MetricCard label="Open interest" value={formatInteger(l?.openInterest)} tone={l ? 'flat' : 'muted'} hint="Total open contracts" />
            <MetricCard
              label="Spec. 3Y percentile"
              value={l?.nonCommercialPercentile != null ? formatPercent(l.nonCommercialPercentile, 0).replace('%', '') : PLACEHOLDER}
              tone={l ? 'flat' : 'muted'}
              hint="Large speculator net vs trailing 3 years"
            />
          </section>

          <section aria-labelledby="informed-title" className="flex flex-col gap-3">
            <h2 id="informed-title" className="eyebrow text-gold">
              Informed money analysis
            </h2>
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <GroupComparisonChart rows={rows} title="Commercial vs large speculator vs non-reportable" />
              <PercentileChart rows={rows} />
              <OpenInterestChart rows={rows} />
              <Card>
                <CardHeader title="Positioning vs price behaviour" subtitle="Divergence between positioning and price requires aligned weekly closes from the market-data backend." />
                <CardBody className="grid gap-3 sm:grid-cols-2">
                  <FeaturePending title="Positioning divergence" requires="Weekly price series + COT Intelligence Agent" />
                  <FeaturePending title="Positioning trend regime" requires="COT Intelligence Agent" />
                </CardBody>
              </Card>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
