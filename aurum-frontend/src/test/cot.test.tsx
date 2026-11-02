import { afterEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { InternalAxiosRequestConfig } from 'axios';
import COTAnalysisPage from '@/pages/COTAnalysisPage';
import InstitutionalIntelligencePage from '@/pages/InstitutionalIntelligencePage';
import { getCotAnalysis, getCotHistory, getCotStatus } from '@/services/cotService';
import { filterCotSeries } from '@/hooks/useCotAnalysis';
import { apiClient } from '@/services/api';
import { mockApi, offlineAdapter } from './mockApi';
import { renderWithProviders } from './render';
import type { CotAnalysis, CotHistory, CotInstrument, CotStatus } from '@/types/cot';

const originalAdapter = apiClient.defaults.adapter;
afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
});

// Shapes mirror the FastAPI responses (values are test fixtures, not CFTC data).
const instruments: CotInstrument[] = [
  {
    instrument: 'EURUSD',
    mappings: [
      { reportType: 'legacy', cftcContractName: 'EURO FX - CHICAGO MERCANTILE EXCHANGE', cftcContractCode: '099741', exchange: 'CME', contractDescription: 'Euro FX futures', currency: 'USD', mappingStatus: 'verified', verificationSource: 'CFTC history file', verifiedAt: '2026-11-02', primaryCategory: 'non_commercial', primaryCategoryLabel: 'Non-commercial' },
      { reportType: 'tff', cftcContractName: 'EURO FX - CHICAGO MERCANTILE EXCHANGE', cftcContractCode: '099741', exchange: 'CME', contractDescription: 'Euro FX futures', currency: 'USD', mappingStatus: 'verified', verificationSource: 'CFTC history file', verifiedAt: '2026-11-02', primaryCategory: 'leveraged_funds', primaryCategoryLabel: 'Leveraged Funds' },
    ],
  },
];

const status: CotStatus = {
  engine: 'ok', configuredSource: 'auto', cftc: { checked: false, note: 'Pass ?probe=true' },
  datasets: [{ reportType: 'legacy', reports: 2, firstReportDate: '2026-10-20', latestReportDate: '2026-10-27', latestReleaseDate: '2026-10-30', lastRetrieval: '2026-11-02T10:00:00Z' }],
  series: [{ instrument: 'EURUSD', reportType: 'legacy', reports: 2, latestReportDate: '2026-10-27', latestReleaseDate: '2026-10-30', freshness: { status: 'current' } }],
  contractsMonitored: 8, positionsStored: 8, metricsStored: 8, lastSuccessfulIngestion: '2026-11-02T10:00:00Z', lastIngestion: null,
  failedIngestions: 0, pendingUpdates: 0, openIssues: { info: 1 }, runningJobs: [],
};

const pt = (category: string, net: number) => ({ category, long: 1000 + Math.max(net, 0), short: 1000 + Math.max(-net, 0), spreading: category === 'non_commercial' ? 50 : null, net, weeklyNetChange: -10, historicalPercentile36m: 12, cotIndex: 8, positioningStrength: 'strong_bearish' as const });
const history: CotHistory = {
  instrument: 'EURUSD', reportType: 'legacy', primaryCategory: 'non_commercial',
  contract: { name: 'EURO FX - CHICAGO MERCANTILE EXCHANGE', code: '099741', exchange: 'CME', mappingStatus: 'verified' },
  categories: [{ key: 'commercial', label: 'Commercial' }, { key: 'non_commercial', label: 'Non-commercial' }, { key: 'non_reportable', label: 'Non-reportable' }],
  source: 'CFTC Commitments of Traders',
  series: ['2026-10-20', '2026-10-27'].map((d, i) => ({ reportId: i + 1, reportDate: d, releaseDate: d.replace(/(\d+)$/, (x) => String(Number(x) + 3)), releaseTimestamp: `${d}T19:30:00Z`, openInterest: 5000, categories: [pt('commercial', 300), pt('non_commercial', -250), pt('non_reportable', -50)] })),
};

const cat = (category: string, label: string, net: number) => ({
  category, label, long: 1000, short: 1000 - net, spreading: null, netPosition: net, weeklyNetChange: -10, longChange: -5, shortChange: 5, netPositionPctOi: -5, historicalPercentile12m: 20,
  historicalPercentile24m: 15, historicalPercentile36m: 12, historicalPercentile48m: 10, cotIndex: 8, zScore: -1.4, positioningDirection: 'net_short' as const,
  positioningStrength: 'strong_bearish' as const, changeType: 'net_short_accumulation', changeDriver: 'shorts_added', significantChange: false, unusualPositioning: false,
  historyWeeks: 208, trendDirection: 'decreasing' as const, trendReports: 3,
});
const analysis: CotAnalysis = {
  instrument: 'EURUSD', generatedAt: '2026-11-02T12:00:00Z', asOf: null,
  families: [
    {
      reportType: 'legacy', datasetName: 'Legacy - Futures Only', datasetId: '6dca-aqww', datasetIdStatus: 'unverified',
      contract: { name: 'EURO FX - CHICAGO MERCANTILE EXCHANGE', code: '099741', exchange: 'CME', description: '', mappingStatus: 'verified', verificationSource: 'CFTC', verifiedAt: '2026-11-02' },
      primaryCategory: 'non_commercial', available: true, reportDate: '2026-10-27', releaseDate: '2026-10-30', releaseTimestamp: '2026-10-30T19:30:00Z', releaseMethod: 'rule',
      releaseUncertain: false, reportId: 2, openInterest: 5000, freshness: { status: 'current' },
      categories: [cat('commercial', 'Commercial', 300), cat('non_commercial', 'Non-commercial', -250)],
      comparisons: [{ family: 'legacy', groupA: 'commercial', groupB: 'non_commercial', label: 'Commercial vs Non-commercial', groupANet: 300, groupBNet: -250, positioningRelation: 'opposed', flowRelation: null, percentileGap: 10 }],
      divergence: { available: false, reason: 'No candles' },
      statements: { facts: ['Commercial: 1,000 long, 700 short (net +300).'], metrics: ['Non-commercial: weekly net change -10.'], interpretation: ['Non-commercial remain net short; relative to the last 3 years their positioning is strong bearish (12th percentile).'] },
      confidence: { level: 'high', reasons: [] }, dataQualityWarnings: [],
    },
  ],
  overall: { stance: 'neutral', lean: 'bearish', interpretation: 'Overall institutional positioning context for EURUSD: neutral, leaning bearish.', confidence: 'high' },
  disclaimer: 'COT data shows aggregate open futures positions; not a trading signal.',
};

function liveBackend() {
  mockApi({
    'GET /cot/instruments': instruments,
    'GET /cot/status': status,
    'GET /cot/history/EURUSD': history,
    'GET /cot/analysis/EURUSD': analysis,
    'GET /cot/divergence/EURUSD': { instrument: 'EURUSD', reportType: 'legacy', category: 'non_commercial', categoryLabel: 'Non-commercial', available: false, reason: 'Only 0 report(s) overlap with stored price history', windowReports: 4, priceTimeframe: 'D1', priceCoverage: { from: null, to: null }, series: [], signals: [], latest: null, method: 'Price = last completed D1 close' },
    'GET /cot/quality': { items: [{ id: 1, reportType: 'legacy', contractCode: '099741', aurumSymbol: 'EURUSD', reportDate: '2026-10-27', issueType: 'unusual_change', severity: 'info', description: 'Weekly net change is unusual; kept as reported.', detectedAt: '2026-11-02T10:00:00Z', lastSeenAt: '2026-11-02T10:00:00Z', occurrences: 1, resolved: false }], total: 1, page: 1, pageSize: 25 },
    'GET /cot/logs': { items: [{ id: 9, jobId: 'j', reportType: 'legacy', jobType: 'update', periodStart: '2026-10-06', periodEnd: '2026-11-02', source: 'files', startedAt: '2026-11-02T10:00:00Z', finishedAt: '2026-11-02T10:00:05Z', durationSeconds: 5, recordsRetrieved: 32, recordsInserted: 0, recordsUpdated: 0, recordsUnchanged: 32, recordsRejected: 0, status: 'success', errorMessage: null }], total: 1, page: 1, pageSize: 12 },
    'GET /cot/reports': { items: [{ id: 2, reportType: 'legacy', contractCode: '099741', contractMarketName: 'EURO FX - CHICAGO MERCANTILE EXCHANGE', reportDate: '2026-10-27', releaseDate: '2026-10-30', releaseTimestamp: '2026-10-30T19:30:00Z', releaseMethod: 'rule', releaseUncertain: false, retrievalTimestamp: '2026-11-02T10:00:00Z', openInterest: 5000, source: 'files', sourceFile: 'deacot2026.zip' }], total: 1, page: 1, pageSize: 500 },
    'GET /cot/reports/2': { id: 2, reportType: 'legacy', contractCode: '099741', contractMarketName: 'EURO FX - CHICAGO MERCANTILE EXCHANGE', reportDate: '2026-10-27', releaseDate: '2026-10-30', releaseTimestamp: '2026-10-30T19:30:00Z', releaseMethod: 'rule', releaseUncertain: false, retrievalTimestamp: '2026-11-02T10:00:00Z', openInterest: 5000, source: 'files', sourceFile: 'deacot2026.zip', sourcePayload: { 'Market and Exchange Names': 'EURO FX - CHICAGO MERCANTILE EXCHANGE', 'CFTC Contract Market Code': '099741' }, positions: [{ traderCategory: 'commercial', longPositions: 1000, shortPositions: 700, spreadingPositions: null, netPositions: 300 }], metrics: [{ traderCategory: 'commercial', weeklyNetChange: -10, historicalPercentile36m: 12, cotIndex: 8, zScore: -1.4, positioningStrength: 'strong_bearish', calculationVersion: '1.0' }], revisions: [] },
  });
}

describe('COT services', () => {
  it('returns labelled demo data and never a demo interpretation', async () => {
    expect((await getCotStatus({ mock: true })).source).toBe('demo');
    const h = await getCotHistory({ mock: true }, 'EURUSD', 'legacy');
    expect(h.source).toBe('demo');
    expect(h.data?.source).toMatch(/DEMO DATA/);
    expect((await getCotHistory({ mock: true }, 'EURUSD', 'tff')).data).toBeNull();
    expect((await getCotAnalysis({ mock: true }, 'EURUSD')).data).toBeNull();
  });

  it('calls the live backend with typed parameters', async () => {
    let params: unknown;
    mockApi({ 'GET /cot/history/EURUSD': (cfg: InternalAxiosRequestConfig) => ((params = cfg.params), { status: 200, data: history }) });
    const r = await getCotHistory({ mock: false }, 'EURUSD', 'legacy');
    expect(r.source).toBe('live');
    expect(params).toEqual({ reportType: 'legacy' });
  });

  it('filters history by period without recomputing metrics', () => {
    const series = Array.from({ length: 120 }, (_, i) => ({ ...history.series[0]!, reportDate: `2024-01-${String((i % 28) + 1).padStart(2, '0')}` }));
    expect(filterCotSeries(series, '12m', { from: '', to: '' })).toHaveLength(52);
    expect(filterCotSeries(history.series, 'custom', { from: '2026-10-25', to: '' })).toHaveLength(1);
  });
});

describe('COT dashboard (live backend)', () => {
  it('shows mapping, report and release dates, metrics and separated intelligence', async () => {
    liveBackend();
    renderWithProviders(<COTAnalysisPage />, { mock: false });
    expect(await screen.findByText('Non-commercial net')).toBeInTheDocument();
    expect(screen.getAllByText(/099741/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/released 2026-10-30/).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: /Measured data \(CFTC\)/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Calculated metrics \(AURUM\)/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Interpretation/ })).toBeInTheDocument();
    expect(screen.getByText(/12th percentile/)).toBeInTheDocument();
    expect(screen.getByText(/neutral, leaning bearish/)).toBeInTheDocument();
    expect(screen.queryByText(/never produced from demo data/)).not.toBeInTheDocument();
  });

  it('navigates to divergence, quality and the report explorer', async () => {
    liveBackend();
    const user = userEvent.setup();
    renderWithProviders(<COTAnalysisPage />, { mock: false });
    await screen.findByText('Non-commercial net');
    await user.click(screen.getByRole('tab', { name: 'Price divergence' }));
    expect(await screen.findByText(/not evidence of an imminent reversal/i)).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /Data quality/ }));
    expect(await screen.findByText(/kept as reported/)).toBeInTheDocument();
    expect(screen.getByText('32 / 0 / 0 / 0')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Report explorer' }));
    expect(await screen.findByText(/Original CFTC record \(2 fields\)/)).toBeInTheDocument();
    expect(screen.getByText('deacot2026.zip', { exact: false })).toBeInTheDocument();
  });

  it('reports a disconnected backend honestly instead of falling back to demo data', async () => {
    apiClient.defaults.adapter = offlineAdapter;
    renderWithProviders(<COTAnalysisPage />, { mock: false });
    expect((await screen.findAllByText(/Backend disconnected/i)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/synthetic, not CFTC/i)).not.toBeInTheDocument();
  });

  it('institutional intelligence page summarises each instrument from the backend', async () => {
    liveBackend();
    renderWithProviders(<InstitutionalIntelligencePage />, { mock: false });
    const card = (await screen.findAllByText(/leaning bearish/))[0]!;
    expect(card).toBeInTheDocument();
    expect(within(document.body).getAllByText(/not individual institutions/).length).toBe(1);
  });
});
