import type { PerformanceReport, TradeSignal } from './trading';

export type DecisionLabel = 'STRONG_BUY_SETUP' | 'BUY_SETUP' | 'STRONG_SELL_SETUP' | 'SELL_SETUP' | 'WAIT' | 'NO_TRADE' | 'INSUFFICIENT_DATA';

export interface GateRow {
  gate: string;
  result: 'pass' | 'warn' | 'fail';
  evidence: Record<string, unknown>;
  checkedAt: string;
}

export interface EvidenceRow {
  component: string;
  points: number;
  maxPoints: number;
  sentence: string;
}

export interface StatusChange {
  from: string | null;
  to: string;
  actor: string;
  note: string | null;
  at: string;
}

export interface AiReviewBody {
  summary: string;
  bullishCase: string;
  bearishCase: string;
  supportingPoints: string[];
  risks: string[];
  dataGaps: string[];
  invalidationCheck: string;
}

export interface AiReview {
  status: string;
  reason: string | null;
  verdict: 'agree' | 'caution' | 'reject' | null;
  adjustment: number | null;
  review: AiReviewBody | null;
  model?: string | null;
  promptVersion?: string | null;
  createdAt?: string | null;
}

export interface SignalOutcome {
  touched: boolean;
  filled: boolean;
  result: string;
  rMultiple: number | null;
  mfeR: number | null;
  maeR: number | null;
  resolvedAt: string | null;
}

export interface SignalDossier extends TradeSignal {
  takeProfit2: number | null;
  expiresAt: string;
  invalidation: string;
  grade: string;
  deterministicScore: number;
  rejectedBy: string | null;
  reviewStatus: string;
  suggestedVolume: number | null;
  volumeReason: string | null;
  evidenceDetail: EvidenceRow[];
  gates: GateRow[];
  aiReview: AiReview;
  statusHistory: StatusChange[];
  outcome: SignalOutcome | null;
  availability: Record<string, string>;
  disclaimer: string;
}

export interface RegimeView {
  regime: string;
  timeframe: string;
  volatilityRegime?: string | null;
  expansionState?: string | null;
  supporting: string[];
  conflicting: string[];
  quality: string;
  reason?: string;
}

export interface TimeframeBias {
  bias: string;
  weight: number;
  state: string | null;
  reason: string | null;
}

export interface BiasView {
  overall: string;
  agreement: number;
  timeframes: Record<string, TimeframeBias>;
  agreeing: string[];
  disagreeing: string[];
  note: string;
}

export interface EvidenceSummary {
  template: string;
  timeframe: string;
  direction: string;
  finalScore: number;
  grade: string;
  positive: string[];
  negative: string[];
  conflicting: string[];
  missing: string[];
  qualityDeductions: string[];
}

export interface Explanation {
  source: string;
  bullishEvidence: string[];
  bearishEvidence: string[];
  conflicts: string[];
  missingData: string[];
  market: string;
  decision: string;
}

export interface AnalysisSummary {
  candidates: number;
  regime?: RegimeView;
  bias?: BiasView;
  conflicts?: { kind: string; detail: string }[];
  decision?: { decision: DecisionLabel; reasons: string[]; candidateKey: string | null };
  explanation?: Explanation;
  evidence?: EvidenceSummary[];
  humanReview?: string;
}

export interface AnalysisRow {
  id: string;
  symbol: string;
  asOf: string;
  status: string;
  decision: DecisionLabel | null;
  regime: string | null;
  bias: string | null;
  candidates: number;
  createdAt: string;
}

export interface AnalysisDetail extends AnalysisRow {
  availability: Record<string, string>;
  summary: AnalysisSummary | null;
  disclaimer: string;
}

export interface SetupRow {
  strategy: string;
  direction: string;
  instrument: string;
  timeframe: string;
  evidenceGrade: string;
  score: number;
  valid: boolean;
  blockedBy: string | null;
  invalidation: string;
}

export interface SetupScan {
  symbol: string;
  asOf: string;
  setups: SetupRow[];
  skipped: { template: string; timeframe: string | null; reason: string }[];
  disclaimer: string;
}

export interface TradeIdea extends TradeSignal {
  takeProfit2: number | null;
  invalidation: string;
  expiresAt: string;
  grade: string;
  estimatedVolume: number | null;
  volumeIsEstimate: boolean;
  reviewStatus: string;
  humanReview: string;
}

export interface StrategyRow {
  id: string;
  version: string;
  enabled: boolean;
  requireAlignment: boolean;
  timeframes: string[];
}

export interface ProviderRow {
  id: string;
  selected: boolean;
  model: string | null;
  configured: boolean;
  budgetEnforced: boolean;
  note?: string;
}

export interface ReviewerState {
  enabled: boolean;
  blockedReason: string | null;
  provider: string;
  model: string | null;
  budgetEnforced: boolean;
  budgetUsd: number;
  budgetUsedUsd: number;
  maxCallsPerHour: number;
  lastError: { reason: string; at: string } | null;
}

export interface ProvidersView {
  providers: ProviderRow[];
  state: ReviewerState | null;
  disclaimer: string;
}

export interface AiHealth {
  status: string;
  schemaReady: boolean;
  engineVersion: string;
  lastRun: string | null;
  aiBlockedReason: string | null;
  disclaimer: string;
}

export interface GroupStats {
  trades: number;
  winRate: number;
  expectancyR: number;
  profitFactor: number | null;
  totalR: number;
}

export interface EvaluationDetails {
  resultsIn: string;
  assumedRiskPct: number;
  accountStart: number;
  byTemplate: Record<string, GroupStats>;
  byConfidence: Record<string, GroupStats>;
  byTimeframe: Record<string, GroupStats>;
  byRegime: Record<string, GroupStats>;
  warnings: string[];
  steps?: number;
  candidatesSeen?: number;
  blockedByGate?: Record<string, number>;
  stillOpenAtEnd?: number;
  slippageR?: number;
  aiReviewer?: string;
  disclaimer?: string;
}

export interface BacktestReport extends PerformanceReport {
  details?: EvaluationDetails;
  run?: { id: string; symbols: string[]; from: string; to: string };
  disclaimer?: string;
}

export interface EvaluationRun {
  id: string;
  symbols: string[];
  from: string;
  to: string;
  createdAt: string;
  metrics: PerformanceReport['metrics'];
}

export interface JobState {
  jobId: string;
  status: string;
  error?: string | null;
  progress?: string[];
  result?: Record<string, unknown> | null;
}

export type ReviewAction = 'approve' | 'reject' | 'reopen';

export interface ReviewInput {
  reviewer: string;
  note: string;
}
