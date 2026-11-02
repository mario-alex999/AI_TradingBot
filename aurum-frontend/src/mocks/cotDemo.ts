/**
 * ─────────────────────────────  DEMO DATA  ─────────────────────────────
 * Adapts the synthetic Legacy series from ./cot.ts to the Module 02 API shapes so the COT dashboard can be
 * previewed without a backend. It is NOT CFTC data: contract names carry "(demo)", every response is tagged
 * source="demo" by the service layer, and no interpretation is ever generated from it.
 */
import { demoCotReport } from './cot';
import { percentileRank } from '@/utils/cot';
import type { CotCategoryPoint, CotHistory, CotInstrument, CotReportType, CotStatus } from '@/types/cot';

const DEMO_CONTRACTS: Record<string, { code: string; exchange: string }> = {
  EURUSD: { code: '099741', exchange: 'CME' },
  GBPUSD: { code: '096742', exchange: 'CME' },
  XAUUSD: { code: '088691', exchange: 'COMEX' },
  US30: { code: '124603', exchange: 'CBOT' },
};

export function demoInstruments(): CotInstrument[] {
  return Object.entries(DEMO_CONTRACTS).map(([instrument, c]) => ({
    instrument,
    mappings: [
      {
        reportType: 'legacy',
        cftcContractName: `${instrument} futures (demo)`,
        cftcContractCode: c.code,
        exchange: c.exchange,
        contractDescription: 'DEMO DATA - synthetic, not CFTC',
        currency: 'USD',
        mappingStatus: 'demo',
        verificationSource: 'DEMO DATA',
        verifiedAt: null,
        primaryCategory: 'non_commercial',
        primaryCategoryLabel: 'Non-commercial',
      },
    ],
  }));
}

export function demoHistory(instrument: string, reportType: CotReportType): CotHistory | null {
  const rep = demoCotReport(instrument, 208);
  if (!rep || reportType !== 'legacy') return null;
  const nets: number[] = [];
  const series = rep.history.map((r, i) => {
    const groups: [string, number, number, number | null][] = [
      ['commercial', r.commercialLong, r.commercialShort, null],
      ['non_commercial', r.nonCommercialLong, r.nonCommercialShort, 0],
      ['non_reportable', r.nonReportableLong, r.nonReportableShort, null],
    ];
    const ncNet = r.nonCommercialLong - r.nonCommercialShort;
    nets.push(ncNet);
    const window = nets.slice(Math.max(0, i - 155));
    const categories: CotCategoryPoint[] = groups.map(([category, long, short, spreading]) => ({
      category,
      long,
      short,
      spreading,
      net: long - short,
      historicalPercentile36m: category === 'non_commercial' && window.length >= 125 ? percentileRank(window, ncNet) : null,
    }));
    return { reportId: i + 1, reportDate: r.reportDate, releaseDate: r.publicationDate, releaseTimestamp: `${r.publicationDate}T20:30:00Z`, openInterest: r.openInterest, categories };
  });
  return {
    instrument,
    reportType: 'legacy',
    primaryCategory: 'non_commercial',
    contract: { name: rep.contract, code: DEMO_CONTRACTS[instrument]?.code ?? '-', exchange: DEMO_CONTRACTS[instrument]?.exchange ?? '-', mappingStatus: 'demo' },
    categories: [
      { key: 'commercial', label: 'Commercial' },
      { key: 'non_commercial', label: 'Non-commercial' },
      { key: 'non_reportable', label: 'Non-reportable' },
    ],
    source: rep.source,
    series,
  };
}

export function demoStatus(): CotStatus {
  return {
    engine: 'ok',
    configuredSource: 'DEMO DATA - synthetic, not CFTC',
    cftc: { checked: false, note: 'Demo mode: no connection to the CFTC or the AURUM backend.' },
    datasets: [],
    series: [],
    contractsMonitored: 4,
    positionsStored: 0,
    metricsStored: 0,
    lastSuccessfulIngestion: null,
    lastIngestion: null,
    failedIngestions: 0,
    pendingUpdates: 0,
    openIssues: {},
    runningJobs: [],
  };
}
