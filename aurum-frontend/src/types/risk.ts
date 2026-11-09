/**
 * Module 06 (Advanced Risk Management Engine) response shapes, as served by /api/v1/risk (camelCase).
 * The backend is the only source of truth: the browser never computes a risk decision or a lot size.
 */
export type RiskDecision = 'PASS' | 'REVIEW' | 'BLOCK' | 'ERROR';
export type RiskStatus = 'WITHIN_LIMITS' | 'WARNING' | 'BLOCKED' | 'DATA_UNAVAILABLE';
export type RuleSeverity = 'HARD_BLOCK' | 'WARNING' | 'INFORMATIONAL';
export type RuleOutcome = 'pass' | 'fail' | 'error' | 'skipped';
export type RiskProfile = 'conservative' | 'moderate' | 'aggressive' | 'custom';

export interface RuleCheck {
  ruleId: string;
  name: string;
  category: string;
  severity: RuleSeverity;
  outcome: RuleOutcome;
  reasonCode: string;
  message: string;
  evidence: Record<string, unknown>;
  thresholds: Record<string, unknown>;
}

export interface PeriodLoss {
  label: string;
  start: string;
  end: string;
  startValue: number | null;
  startSource: string | null;
  startUncertain: boolean;
  currentValue: number | null;
  netDeposits: number;
  realizedPnl: number;
  loss: number | null;
  lossPct: number | null;
  limitPct: number;
}

export interface StreakTrade {
  id: string;
  symbol: string;
  closedAt: string;
  pnl: number;
  result: 'win' | 'loss' | 'breakeven';
}

export interface RiskAccount {
  overallStatus: RiskStatus;
  configVersion: number;
  profile: RiskProfile;
  activeLocks: number;
  dataStatus: 'ok' | 'stale' | 'missing' | 'invalid';
  reason: string | null;
  ageMinutes: number | null;
  lossBasis: 'equity' | 'balance';
  capturedAt: string | null;
  currency: string | null;
  tradeMode: string | null;
  leverage: number | null;
  balance: number | null;
  equity: number | null;
  floatingPnl: number | null;
  margin: number | null;
  freeMargin: number | null;
  marginLevel: number | null;
  marginUtilizationPct: number | null;
  peakEquity: number | null;
  drawdownPct: number | null;
  maxDrawdownPct: number;
  periods: Partial<Record<'day' | 'week' | 'month', PeriodLoss>>;
  consecutiveLosses: number;
  maxConsecutiveLosses: number;
  streakTrades: StreakTrade[];
  recentResults: StreakTrade[];
  tradesToday: number;
  maxTradesPerDay: number;
  computedAt: string;
}

export interface ExposureRow {
  kind: 'position' | 'reservation';
  ref: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  assetClass: string;
  strategy: string | null;
  risk: number | null;
  riskPct: number | null;
  note: string | null;
  notional: number | null;
}

export interface Correlation {
  a: string;
  b: string;
  coefficient: number | null;
  samples: number;
  lookbackBars: number;
  timeframe: string;
  lastCommonBar: string | null;
  reliable: boolean;
  reason: string | null;
}

export type RiskBucket = Record<string, { risk: number; riskPct: number | null }>;

export interface RiskPortfolio {
  status: RiskStatus;
  concentrationWarnings: string[];
  maxTotalOpenRiskPct: number;
  maxSymbolRiskPct: number;
  maxAssetClassRiskPct: number;
  maxCorrelatedRiskPct: number;
  configVersion: number;
  computedAt: string;
  equity: number | null;
  bounded: boolean;
  totalOpenRisk: number;
  totalOpenRiskPct: number | null;
  unboundedPositions: ExposureRow[];
  positions: ExposureRow[];
  byInstrument: RiskBucket;
  byAssetClass: RiskBucket;
  byDirection: RiskBucket;
  byStrategy: RiskBucket;
  currencyExposure: Record<string, number>;
  correlations: Correlation[];
}

export interface RiskLockEvent {
  action: string;
  at: string;
  actor: string;
  reason: string | null;
}

export interface RiskLock {
  lockId: string;
  scope: 'global' | 'account' | 'instrument' | 'strategy';
  scopeValue: string | null;
  reason: string;
  triggerSource: string;
  ruleId: string | null;
  status: 'active' | 'released' | 'expired';
  activatedAt: string;
  activatedBy: string;
  expiresAt: string | null;
  releasedAt: string | null;
  releasedBy: string | null;
  releaseReason: string | null;
  history: RiskLockEvent[];
}

export interface RiskLocks {
  active: RiskLock[];
  history: RiskLock[];
}

export interface Calculation {
  accountCurrency: string;
  valuePerPriceUnitPerLot: number;
  valueMethod: string;
  conversion: Record<string, unknown> | null;
  stopDistance: number;
  takeProfitDistance: number | null;
  spreadPrice: number | null;
  slippagePrice: number;
  commissionPerLot: number;
  lossPerLotAtStop: number;
  costPerLot: number;
  totalLossPerLot: number;
  riskReward: number | null;
  adjustedRiskReward: number | null;
  riskPctBudget: number;
  maxRiskAmount: number;
  rawVolume: number;
  recommendedVolume: number | null;
  finalVolume: number | null;
  finalVolumeSource: string;
  cappedByMaxVolume: boolean;
  minVolumeRiskPct: number | null;
  estimatedLoss: number | null;
  estimatedLossPct: number | null;
  estimatedReward: number | null;
  transactionCost: number | null;
  notional: number | null;
  marginEstimate: number | null;
  marginMethod: string | null;
  notes: string[];
}

export interface PositionSizeResult {
  symbol: string;
  direction: 'BUY' | 'SELL';
  valid: boolean;
  equitySource: 'request' | 'account_mirror';
  accountCurrency: string | null;
  configVersion: number;
  calculation: Calculation | null;
  checks: RuleCheck[];
  failedChecks: RuleCheck[];
  warnings: RuleCheck[];
  note: string;
  disclaimer: string;
}

export interface TradeProposalInput {
  symbol: string;
  direction: 'BUY' | 'SELL';
  entry: number;
  stopLoss: number;
  takeProfit?: number;
  riskPct?: number;
  requestedVolume?: number;
  equity?: number;
  accountCurrency?: string;
  strategy?: string;
}

export interface Module07Contract {
  eligibleForHumanReview: boolean;
  fullyRiskApproved: boolean;
  mustDisplayWarnings: boolean;
  hardBlock: boolean;
  note: string;
}

export interface Assessment {
  assessmentId: string;
  decision: RiskDecision;
  instrument: string;
  direction: 'BUY' | 'SELL';
  strategy: string | null;
  recommendedVolume: number | null;
  finalVolume: number | null;
  riskPct: number | null;
  monetaryRisk: number | null;
  potentialReward: number | null;
  riskReward: number | null;
  accountCurrency: string | null;
  calculation: Calculation | null;
  ruleViolations: RuleCheck[];
  warnings: RuleCheck[];
  errors: RuleCheck[];
  information: RuleCheck[];
  activeLocks: RiskLock[];
  configVersion: number;
  configProfile: RiskProfile;
  engineVersion: string;
  timestamp: string;
  expiresAt: string;
  correlationId: string;
  createdBy: string;
  state: 'active' | 'consumed' | 'cancelled';
  replayed: boolean;
  module07: Module07Contract;
  disclaimer: string;
}

export interface AssessmentSummary {
  assessmentId: string;
  tradeIdeaId: string | null;
  instrument: string;
  direction: 'BUY' | 'SELL';
  strategy: string | null;
  decision: RiskDecision;
  state: string;
  recommendedVolume: number | null;
  finalVolume: number | null;
  monetaryRisk: number | null;
  riskPct: number | null;
  riskReward: number | null;
  configVersion: number;
  createdAt: string;
  expiresAt: string;
  source: string;
  ruleViolations: string[];
  warnings: string[];
  errors: string[];
}

export interface AssessmentPage {
  total: number;
  limit: number;
  offset: number;
  items: AssessmentSummary[];
}

export interface HistoryFilters {
  decision?: RiskDecision | '';
  instrument?: string;
  rule?: string;
  limit: number;
  offset: number;
}

/** The editable numeric limits. Everything else in the config is shown read-only. */
export interface RiskLimits {
  riskPerTradePct: number;
  maxRiskPerTradePct: number;
  maxDailyLossPct: number;
  maxWeeklyLossPct: number;
  maxMonthlyLossPct: number;
  maxDrawdownPct: number;
  maxOpenPositions: number;
  maxTotalOpenRiskPct: number;
  maxSymbolRiskPct: number;
  maxAssetClassRiskPct: number;
  maxCorrelatedRiskPct: number;
  maxConsecutiveLosses: number;
  maxTradesPerDay: number;
  minRiskReward: number;
  maxMarginUtilizationPct: number;
}

export interface RiskConfig extends RiskLimits {
  profile: RiskProfile;
  lossBasis: 'equity' | 'balance';
  tradingDay: { timezone: string; rollover: string };
  assessmentTtlMinutes: number;
  resetPolicy: Record<string, string>;
  [key: string]: unknown;
}

export interface ConfigVersion {
  version: number;
  profile: RiskProfile;
  fingerprint: string;
  createdBy: string;
  createdAt: string;
  reason: string;
  weakened: string[];
}

export interface RiskConfigView {
  version: number;
  meta: ConfigVersion;
  config: RiskConfig;
  profiles: RiskProfile[];
  history: ConfigVersion[];
}

export interface ConfigUpdate {
  baseVersion: number;
  reason: string;
  profile?: RiskProfile;
  changes?: Partial<RiskLimits>;
  acknowledgeWeakening?: boolean;
}

export interface RiskHealth {
  engineVersion: string;
  status: 'ok' | 'degraded' | 'unavailable';
  canAssess: boolean;
  configVersion: number;
  checkedAt: string;
  note: string;
  dependencies: Record<string, unknown>;
}
