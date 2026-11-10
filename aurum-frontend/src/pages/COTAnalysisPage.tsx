import { useEffect, useMemo, useState } from 'react';
import { AdminJobButton } from '@/components/common/AdminJobButton';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody } from '@/components/common/Card';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { EmptyState } from '@/components/common/EmptyState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { Tabs } from '@/components/common/Tabs';
import { CotOverview } from '@/components/cot/CotOverview';
import { CotInstrumentSelector } from '@/components/cot/CotInstrumentSelector';
import { CotSummary } from '@/components/cot/CotSummary';
import { CotIntelligence } from '@/components/cot/CotIntelligence';
import { CotDivergencePanel } from '@/components/cot/CotDivergencePanel';
import { CotQualityPanel } from '@/components/cot/CotQualityPanel';
import { CotReportExplorer } from '@/components/cot/CotReportExplorer';
import { CotExportButtons } from '@/components/cot/CotExportButtons';
import { CategoryBreakdownChart, CotIndexChart, LongShortChart, NetHistoryChart, OpenInterestChart, PercentileChart, WeeklyChangeChart } from '@/components/cot/CotCharts';
import { useCotAnalysisReport, useCotDivergence, useCotHistory, useCotInstruments, useCotStatus } from '@/hooks/queries';
import { filterCotSeries, type CotPeriod } from '@/hooks/useCotAnalysis';
import { REPORT_TYPE_LABEL, type CotReportType } from '@/types/cot';

type Tab = 'positioning' | 'history' | 'intelligence' | 'divergence' | 'quality' | 'explorer';
const TABS: { id: Tab; label: string }[] = [
  { id: 'positioning', label: 'Positioning' },
  { id: 'history', label: 'Historical charts' },
  { id: 'intelligence', label: 'Intelligence' },
  { id: 'divergence', label: 'Price divergence' },
  { id: 'quality', label: 'Data quality & updates' },
  { id: 'explorer', label: 'Report explorer' },
];
const PERIODS: { value: CotPeriod; label: string }[] = [
  { value: '12m', label: '12M' },
  { value: '2y', label: '2Y' },
  { value: '3y', label: '3Y' },
  { value: '4y', label: '4Y' },
  { value: 'custom', label: 'Custom' },
];

export default function COTAnalysisPage() {
  const [instrument, setInstrument] = useState('EURUSD');
  const [tab, setTab] = useState<Tab>('positioning');
  const [period, setPeriod] = useState<CotPeriod>('2y');
  const [custom, setCustom] = useState({ from: '', to: '' });
  const [probe, setProbe] = useState(false);

  const instruments = useCotInstruments();
  const mappings = useMemo(() => instruments.data?.data.find((i) => i.instrument === instrument)?.mappings ?? [], [instruments.data, instrument]);
  const [reportType, setReportType] = useState<CotReportType>('legacy');
  useEffect(() => {
    if (mappings.length && !mappings.some((m) => m.reportType === reportType)) setReportType(mappings[0]!.reportType);
  }, [mappings, reportType]);

  const status = useCotStatus(probe);
  const history = useCotHistory(instrument, reportType);
  const analysis = useCotAnalysisReport(instrument);
  const divergence = useCotDivergence(instrument, reportType);
  const source = history.data?.source;
  const demo = source === 'demo';
  const hist = history.data?.data ?? null;
  const rows = useMemo(() => (hist ? filterCotSeries(hist.series, period, custom) : []), [hist, period, custom]);
  const family = analysis.data?.data?.families.find((f) => f.reportType === reportType);
  const chartProps = hist ? { series: rows, categories: hist.categories, primary: hist.primaryCategory } : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="COT Analysis"
        description="CFTC Commitments of Traders positioning. Positions are as of Tuesday and become usable only when the CFTC releases them (normally Friday 15:30 ET). Medium- to long-term context, not an entry signal."
        actions={
          <>
            <AdminJobButton action="cotUpdate" invalidate={['cot-status', 'cot-history', 'cot-analysis', 'cot-report', 'cot-reports', 'cot-divergence', 'cot-quality', 'cot-logs']} />
            <DataSourceBadge source={source} fetchedAt={history.data?.fetchedAt} />
          </>
        }
      />
      <CotOverview status={status.data?.data} demo={demo} probing={probe && status.isFetching} onProbe={() => setProbe(true)} />
      {instruments.isError ? (
        <Card>
          <ErrorState error={instruments.error} onRetry={() => void instruments.refetch()} />
        </Card>
      ) : (
        <CotInstrumentSelector instruments={instruments.data?.data ?? []} value={instrument} onChange={setInstrument} />
      )}

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          {mappings.length > 1 && (
            <SegmentedControl
              label="Report family"
              value={reportType}
              onChange={(v) => setReportType(v)}
              options={mappings.map((m) => ({ value: m.reportType, label: REPORT_TYPE_LABEL[m.reportType] }))}
            />
          )}
          <SegmentedControl label="Historical period" value={period} onChange={setPeriod} options={PERIODS} />
          {period === 'custom' && (
            <div className="flex items-end gap-2">
              <label className="flex flex-col gap-1 text-2xs text-neutral">
                From
                <input type="date" className="field num h-8 w-36 text-xs" value={custom.from} onChange={(e) => setCustom({ ...custom, from: e.target.value })} />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-neutral">
                To
                <input type="date" className="field num h-8 w-36 text-xs" value={custom.to} onChange={(e) => setCustom({ ...custom, to: e.target.value })} />
              </label>
            </div>
          )}
        </div>
        <CotExportButtons instrument={instrument} demo={demo} />
      </div>

      {hist && (
        <p className="text-2xs text-neutral">
          {hist.contract.name} ({hist.contract.code}, {hist.contract.exchange}) · {REPORT_TYPE_LABEL[hist.reportType]} · source: {hist.source} · {hist.series.length} reports stored
        </p>
      )}

      <Tabs tabs={TABS} value={tab} onChange={setTab} label="COT dashboard sections" />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="flex flex-col gap-5">
        {history.isPending ? (
          <SkeletonRows rows={8} />
        ) : history.isError ? (
          <Card>
            <ErrorState error={history.error} onRetry={() => void history.refetch()} />
          </Card>
        ) : !hist || hist.series.length === 0 ? (
          <Card>
            <EmptyState
              title={`No ${REPORT_TYPE_LABEL[reportType]} COT data for ${instrument}`}
              description={demo ? 'Demo data covers the Legacy report only.' : 'Run: python -m aurum.cli cot backfill --years 4'}
            />
          </Card>
        ) : (
          <>
            {tab === 'positioning' && chartProps && (
              <>
                {family?.available ? <CotSummary family={family} /> : null}
                <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
                  <CategoryBreakdownChart {...chartProps} />
                  <CotIntelligence analysis={analysis.data?.data} family={family} source={analysis.data?.source} />
                </div>
              </>
            )}
            {tab === 'history' && chartProps && (
              <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                <NetHistoryChart {...chartProps} />
                <LongShortChart {...chartProps} />
                <WeeklyChangeChart {...chartProps} />
                <OpenInterestChart series={rows} />
                <PercentileChart {...chartProps} />
                <CotIndexChart {...chartProps} />
              </div>
            )}
          </>
        )}
        {tab === 'intelligence' &&
          (analysis.isError ? (
            <Card>
              <ErrorState error={analysis.error} onRetry={() => void analysis.refetch()} />
            </Card>
          ) : (
            <CotIntelligence analysis={analysis.data?.data} family={family} source={analysis.data?.source} />
          ))}
        {tab === 'divergence' &&
          (divergence.isError ? (
            <Card>
              <ErrorState error={divergence.error} onRetry={() => void divergence.refetch()} />
            </Card>
          ) : (
            <CotDivergencePanel data={divergence.data?.data} demo={divergence.data?.source === 'demo'} />
          ))}
        {tab === 'quality' && <CotQualityPanel status={status.data?.data} instrument={instrument} />}
        {tab === 'explorer' && <CotReportExplorer instrument={instrument} reportType={reportType} demo={demo} />}
      </div>
      {analysis.data?.data && !demo && (
        <Card>
          <CardBody className="text-xs text-ink-muted">
            <span className="eyebrow mr-2">Overall ({analysis.data.data.overall.confidence} confidence)</span>
            {analysis.data.data.overall.interpretation}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
