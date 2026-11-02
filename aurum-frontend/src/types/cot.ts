/**
 * Typed contracts for the AURUM backend COT endpoints (/api/v1/cot/*, Module 02).
 * Field names mirror the FastAPI responses (camelCase). Dates are ISO strings; timestamps are UTC.
 */
import type { Paginated } from './system';

export type CotReportType = 'legacy' | 'tff' | 'disaggregated';
export type CotSeverity = 'info' | 'warning' | 'error' | 'critical';
export type CotStrength =
  | 'extremely_bullish'
  | 'strong_bullish'
  | 'moderately_bullish'
  | 'neutral'
  | 'moderately_bearish'
  | 'strong_bearish'
  | 'extremely_bearish';
export type CotFreshnessStatus = 'current' | 'update_due' | 'stale' | 'no_data';

export const REPORT_TYPE_LABEL: Record<CotReportType, string> = {
  legacy: 'Legacy',
  tff: 'Traders in Financial Futures',
  disaggregated: 'Disaggregated',
};

export interface CotFreshness {
  status: CotFreshnessStatus;
  ageDays?: number;
  nextExpectedReportDate?: string;
  nextExpectedRelease?: string;
}

export interface CotMapping {
  reportType: CotReportType;
  cftcContractName: string;
  cftcContractCode: string;
  exchange: string;
  contractDescription: string;
  currency: string;
  mappingStatus: string;
  verificationSource: string;
  verifiedAt: string | null;
  primaryCategory: string;
  primaryCategoryLabel: string | null;
}

export interface CotInstrument {
  instrument: string;
  mappings: CotMapping[];
}

export interface CotSourceCheck {
  dataset: CotReportType;
  source: 'socrata' | 'files';
  ok: boolean;
  error?: string;
  latest?: string | null;
  file?: string;
}

export interface CotJob {
  jobId: string;
  type: 'backfill' | 'update' | 'calculate';
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  progress: string[];
  error: string | null;
}

export interface CotIngestionLog {
  id: number;
  jobId: string;
  reportType: CotReportType | null;
  jobType: 'backfill' | 'update' | 'calculate';
  periodStart: string | null;
  periodEnd: string | null;
  source: string | null;
  startedAt: string;
  finishedAt: string | null;
  durationSeconds: number | null;
  recordsRetrieved: number;
  recordsInserted: number;
  recordsUpdated: number;
  recordsUnchanged: number;
  recordsRejected: number;
  status: 'running' | 'success' | 'partial' | 'failed';
  errorMessage: string | null;
}

export interface CotStatus {
  engine: 'ok' | 'empty';
  configuredSource: string;
  cftc: { checked: boolean; note?: string; sources?: CotSourceCheck[]; apiReachable?: boolean; filesReachable?: boolean };
  datasets: {
    reportType: CotReportType;
    reports: number;
    firstReportDate: string;
    latestReportDate: string;
    latestReleaseDate: string;
    lastRetrieval: string;
  }[];
  series: {
    instrument: string;
    reportType: CotReportType;
    reports: number;
    latestReportDate?: string;
    latestReleaseDate?: string;
    freshness: CotFreshness;
  }[];
  contractsMonitored: number;
  positionsStored: number;
  metricsStored: number;
  lastSuccessfulIngestion: string | null;
  lastIngestion: CotIngestionLog | null;
  failedIngestions: number;
  pendingUpdates: number;
  openIssues: Partial<Record<CotSeverity, number>>;
  runningJobs: CotJob[];
}

export interface CotCategoryPoint {
  category: string;
  long: number | null;
  short: number | null;
  spreading: number | null;
  net: number | null;
  weeklyNetChange?: number | null;
  netPositionPctOi?: number | null;
  historicalPercentile12m?: number | null;
  historicalPercentile24m?: number | null;
  historicalPercentile36m?: number | null;
  historicalPercentile48m?: number | null;
  cotIndex?: number | null;
  zScore?: number | null;
  positioningStrength?: CotStrength | null;
  changeType?: string | null;
}

export interface CotHistory {
  instrument: string;
  reportType: CotReportType;
  primaryCategory: string;
  contract: { name: string; code: string; exchange: string; mappingStatus: string };
  categories: { key: string; label: string }[];
  source: string;
  series: {
    reportId: number;
    reportDate: string;
    releaseDate: string;
    releaseTimestamp: string;
    openInterest: number | null;
    categories: CotCategoryPoint[];
  }[];
}

export interface CotCategoryAnalysis {
  category: string;
  label: string;
  long: number | null;
  short: number | null;
  spreading: number | null;
  netPosition: number | null;
  weeklyNetChange: number | null;
  longChange: number | null;
  shortChange: number | null;
  netPositionPctOi: number | null;
  historicalPercentile12m: number | null;
  historicalPercentile24m: number | null;
  historicalPercentile36m: number | null;
  historicalPercentile48m: number | null;
  cotIndex: number | null;
  zScore: number | null;
  positioningDirection: 'net_long' | 'net_short' | 'balanced' | null;
  positioningStrength: CotStrength | null;
  changeType: string | null;
  changeDriver: string | null;
  significantChange: boolean;
  unusualPositioning: boolean;
  historyWeeks: number;
  trendDirection: 'increasing' | 'decreasing' | null;
  trendReports: number;
}

export interface CotComparison {
  family: CotReportType;
  groupA: string;
  groupB: string;
  label: string;
  groupANet: number;
  groupBNet: number;
  positioningRelation: 'opposed' | 'aligned' | 'mixed';
  flowRelation: 'diverging_flows' | 'same_direction_flows' | null;
  percentileGap: number | null;
}

export interface CotDivergenceWindow {
  reportDate: string;
  windowStartReportDate: string;
  priceDate: string;
  windowStartPriceDate: string;
  priceChangePct: number;
  netPositionChange: number;
  signals: ('bearish_divergence' | 'bullish_divergence' | 'unconfirmed_high' | 'unconfirmed_low')[];
  reportsInWindow: number;
}

export interface CotFamilyAnalysis {
  reportType: CotReportType;
  datasetName: string;
  datasetId: string;
  datasetIdStatus: string;
  contract: { name: string; code: string; exchange: string; description: string; mappingStatus: string; verificationSource: string; verifiedAt: string | null };
  primaryCategory: string;
  available: boolean;
  reason?: string;
  reportDate?: string;
  releaseDate?: string;
  releaseTimestamp?: string;
  releaseMethod?: string;
  releaseUncertain?: boolean;
  reportId?: number;
  openInterest?: number | null;
  freshness?: CotFreshness;
  categories?: CotCategoryAnalysis[];
  comparisons?: CotComparison[];
  divergence?: { available: boolean; reason: string | null; category?: string; latest?: CotDivergenceWindow | null; recentSignals?: CotDivergenceWindow[] };
  statements?: { facts: string[]; metrics: string[]; interpretation: string[] };
  confidence?: { level: 'high' | 'medium' | 'low'; reasons: string[] };
  dataQualityWarnings?: { severity: CotSeverity; issueType: string; reportDate: string | null; description: string }[];
}

export interface CotAnalysis {
  instrument: string;
  generatedAt: string;
  asOf: string | null;
  families: CotFamilyAnalysis[];
  overall: { stance: 'bullish' | 'bearish' | 'neutral' | 'mixed' | 'unavailable'; lean: 'bullish' | 'bearish' | null; interpretation: string; confidence: 'high' | 'medium' | 'low' };
  disclaimer: string;
}

export interface CotDivergence {
  instrument: string;
  reportType: CotReportType;
  category: string;
  categoryLabel: string;
  available: boolean;
  reason: string | null;
  windowReports: number;
  priceTimeframe: string;
  priceCoverage: { from: string | null; to: string | null };
  series: { reportDate: string; netPosition: number | null; priceClose: number | null; priceDate: string | null }[];
  signals: CotDivergenceWindow[];
  latest: CotDivergenceWindow | null;
  method: string;
}

export interface CotQualityIssue {
  id: number;
  reportType: CotReportType | null;
  contractCode: string | null;
  aurumSymbol: string | null;
  reportDate: string | null;
  issueType: string;
  severity: CotSeverity;
  description: string;
  detectedAt: string;
  lastSeenAt: string;
  occurrences: number;
  resolved: boolean;
}

export interface CotReportRow {
  id: number;
  reportType: CotReportType;
  contractCode: string;
  contractMarketName: string;
  reportDate: string;
  releaseDate: string;
  releaseTimestamp: string;
  releaseMethod: string;
  releaseUncertain: boolean;
  retrievalTimestamp: string;
  openInterest: number | null;
  source: string;
  sourceFile: string | null;
}

export interface CotReportDetail extends CotReportRow {
  sourcePayload: Record<string, unknown>;
  positions: { traderCategory: string; longPositions: number | null; shortPositions: number | null; spreadingPositions: number | null; netPositions: number | null }[];
  metrics: Record<string, unknown>[];
  revisions: { detectedAt: string; previousHash: string; newHash: string }[];
}

export type CotPaginated<T> = Paginated<T>;
export type CotExportKind = 'raw' | 'positions' | 'metrics' | 'analysis' | 'quality';
