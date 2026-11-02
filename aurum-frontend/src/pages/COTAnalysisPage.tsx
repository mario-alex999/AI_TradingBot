import { useState } from 'react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody } from '@/components/common/Card';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { EmptyState } from '@/components/common/EmptyState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { CotControls } from '@/components/cot/CotControls';
import { CotBreakdown } from '@/components/cot/CotBreakdown';
import { CotInterpretation } from '@/components/cot/CotInterpretation';
import { GroupComparisonChart, NetPositioningChart, OpenInterestChart, PercentileChart, PositioningChangeChart } from '@/components/cot/CotCharts';
import { useCotAnalysis, type CotPeriod } from '@/hooks/useCotAnalysis';
import { formatDateTime, formatInteger, PLACEHOLDER } from '@/utils/format';

export default function COTAnalysisPage() {
  const [instrument, setInstrument] = useState('EURUSD');
  const [period, setPeriod] = useState<CotPeriod>('2y');
  const [custom, setCustom] = useState({ from: '', to: '' });
  const { query, report, rows, latest, source } = useCotAnalysis(instrument, period, custom);

  const overview: [string, string][] = [
    ['Report type', report?.reportType ?? PLACEHOLDER],
    ['Report date (as of)', latest?.reportDate ?? PLACEHOLDER],
    ['Publication date', latest?.publicationDate ?? PLACEHOLDER],
    ['Contract', report?.contract ?? PLACEHOLDER],
    ['Open interest', formatInteger(latest?.openInterest)],
    ['Source', report?.source ?? PLACEHOLDER],
  ];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="COT Analysis"
        description="Commitments of Traders positioning. Reports describe positions held as of Tuesday and are published the following Friday."
        actions={<DataSourceBadge source={source} fetchedAt={query.data?.fetchedAt} />}
      />
      <CotControls instrument={instrument} onInstrument={setInstrument} period={period} onPeriod={setPeriod} custom={custom} onCustom={setCustom} />

      {query.isPending ? (
        <SkeletonRows rows={8} />
      ) : query.isError ? (
        <Card>
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </Card>
      ) : !report ? (
        <Card>
          <EmptyState title={`No COT report for ${instrument}`} description="The backend has not ingested Commitments of Traders data for this contract." />
        </Card>
      ) : (
        <>
          <Card>
            <CardBody>
              <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-6">
                {overview.map(([k, v]) => (
                  <div key={k}>
                    <dt className="eyebrow">{k}</dt>
                    <dd className="num mt-1 text-sm text-ink">{v}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-3 text-2xs text-neutral">Updated {formatDateTime(report.updatedAt)} UTC</p>
            </CardBody>
          </Card>
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            <CotBreakdown latest={latest} />
            <CotInterpretation source={source} hasData={rows.length > 0} />
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <NetPositioningChart rows={rows} />
            <GroupComparisonChart rows={rows} />
            <OpenInterestChart rows={rows} />
            <PositioningChangeChart rows={rows} />
          </div>
          <PercentileChart rows={rows} />
        </>
      )}
    </div>
  );
}
