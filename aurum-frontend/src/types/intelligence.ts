/**
 * Module 04 (Trading Sessions and Market Intelligence) response shapes, mirroring
 * /api/v1/intelligence/*. Every value is calculated by the backend from stored data
 * or received from a named provider; the frontend never derives or invents them.
 */

export interface SessionInterval {
  name: string;
  kind: string;
  startUtc: string;
  endUtc: string;
  localDate: string;
  timezone: string | null;
  durationMinutes: number;
  minutesUntilOpen?: number;
}

export type WindowStatus = 'open' | 'pre_session' | 'post_session' | 'closed' | 'market_closed';

export interface SessionWindow {
  name: string;
  kind: 'session' | 'kill_zone';
  timezone: string;
  localTime: string;
  localOpen: string;
  localClose: string;
  utcOffsetHours: number;
  dst: boolean;
  status: WindowStatus;
  active: boolean;
  marketOpen: boolean | null;
  current: SessionInterval | null;
  next: SessionInterval | null;
  previous: SessionInterval | null;
  minutesUntilOpen: number | null;
  minutesUntilClose: number | null;
  minutesSinceClose: number | null;
}

export interface OverlapWindow {
  name: string;
  priority: number;
  sessions: string[];
  kind: 'overlap' | 'transition';
  active: boolean;
  current: SessionInterval | null;
  next: SessionInterval | null;
  previous: SessionInterval | null;
  minutesRemaining: number | null;
  minutesUntilStart: number | null;
}

export interface SessionsSnapshot {
  asOf: string;
  symbol: string | null;
  marketOpen: boolean | null;
  activeSessions: string[];
  upcomingSession: SessionInterval | null;
  previousSession: SessionInterval | null;
  inOverlap: boolean;
  activeKillZones: string[];
  nextKillZone: SessionInterval | null;
  sessions: SessionWindow[];
  overlaps: OverlapWindow[];
  killZones: SessionWindow[];
  note: string;
  configFingerprint: string;
}

export interface SessionLevel {
  symbol: string;
  levelType: string;
  side: 'high' | 'low';
  price: number;
  sourceCandleTime: string;
  formedAt: string;
  method: string;
  note: string | null;
  period: string;
  periodLabel: string;
  periodStart: string;
  periodEnd: string;
  candles: number | null;
  expectedCandles: number | null;
  missingCandles: number | null;
  complete: boolean;
  provisional: boolean;
  timeframe: string;
  state: 'untouched' | 'swept' | 'broken' | 'provisional' | string;
  sweptAt: string | null;
  brokenAt: string | null;
}

export interface LevelIssue {
  issue: string;
  level?: string;
  message?: string;
  periodStart?: string;
}

export interface SessionLevels {
  symbol: string;
  asOf: string;
  levels: SessionLevel[];
  issues: LevelIssue[];
  rules: string;
}

export interface LiquidityEvent {
  eventTime: string;
  sourceCandleTime: string;
  eventType: 'wick_sweep' | 'confirmed_breakout' | 'false_breakout_candidate' | 'unconfirmed' | 'range_expansion' | string;
  confirmationTime: string | null;
  session: string;
  levelType: string;
  level: number | null;
  side: string;
  direction: string | null;
  timeframe: string;
  rule: string;
  extreme?: number | null;
  close?: number | null;
  relatedStructureEvent: { eventType?: string; direction?: string; scope?: string; timeframe?: string; brokenLevel?: number; breakCandleTime?: string; confirmedAt?: string } | null;
  interpretation: string;
}

export interface SessionLiquidity {
  symbol: string;
  asOf: string;
  events: LiquidityEvent[];
  notes: string[];
  module03: { available: boolean; reason: string | null; timeframe: string | null };
  rules: Record<string, string>;
  interpretation: string;
}

export interface CurrencyCoverage {
  covered: boolean;
  providers: string[];
  missing: string[];
}

export interface CalendarCoverage {
  complete: boolean;
  /** full = third-party calendar with consensus; official = agency + central-bank schedules (gaps listed) */
  level: 'full' | 'official' | 'partial' | 'none';
  providers: string[];
  reason: string;
  currencies?: Record<string, CurrencyCoverage>;
  gaps?: Record<string, string[]>;
  symbolCurrencies?: string[];
  uncoveredCurrencies?: string[];
}

export interface AffectedInstrument {
  instrument: string;
  relation: string;
  currency: string | null;
  explanation: string;
  direction: null;
}

export interface EconomicEvent {
  id: number;
  eventUid: string;
  provider: string;
  providerEventId: string;
  name: string;
  country: string | null;
  currency: string | null;
  category: string;
  subcategory: string | null;
  importance: 'high' | 'medium' | 'low' | null;
  importanceSource: string;
  scheduledAt: string;
  originalScheduledAt: string | null;
  sourceTimezone: string | null;
  timePrecision: string;
  actual: number | null;
  consensus: number | null;
  previous: number | null;
  revisedPrevious: number | null;
  unit: string | null;
  status: string;
  sourceUrl: string | null;
  retrievedAt: string;
  affectedInstruments: AffectedInstrument[];
  qualityStatus: string;
  minutesUntilRelease: number;
  state?: EventRiskState;
}

export interface CalendarPage {
  items: EconomicEvent[];
  page: number;
  pageSize: number;
  total: number;
  coverage: CalendarCoverage;
  asOf: string;
  pointInTime: boolean;
}

export type EventRiskState = 'NORMAL' | 'EVENT_APPROACHING' | 'HIGH_EVENT_RISK' | 'RELEASE_WINDOW' | 'POST_EVENT_VOLATILITY' | 'UNKNOWN';

export interface EventRisk {
  symbol: string;
  asOf: string;
  state: EventRiskState;
  reason: string;
  nextEvent: EconomicEvent | null;
  activeEvents: EconomicEvent[];
  upcomingEvents: EconomicEvent[];
  coverage: CalendarCoverage;
  note: string;
  historicalContext: { samples: number; meanRatio: number | null; [k: string]: unknown } | null;
}

export interface CalendarProviderStatus {
  provider: string;
  configured: boolean;
  requiresKey: boolean;
  coverage: string;
  access?: string;
  status: string;
  lastSuccessAt: string | null;
  lastAttemptAt: string | null;
  lastError: string | null;
  lastEventCount: number | null;
}

export interface CentralBank {
  bank: string;
  name: string;
  currency: string;
  officialUrl: string;
  rateSource: string | null;
  nextMeeting: { date: string; announcementUtc: string; timePrecision: string; source: string; verifiedOn: string } | null;
  status: 'ok' | 'not_sourced' | 'no_data' | 'unavailable';
  reason?: string | null;
  currentRate: number | null;
  currentRateLower?: number | null;
  effectiveDate?: string;
  lastChangeEffective?: string | null;
  previousRate?: number | null;
  previousRateLower?: number | null;
  latestDecision?: { decisionDate: string; status: string; changeBps: number | null; previousRate: number | null; newRate: number | null } | null;
  source?: string;
  sourceUrl?: string;
  retrievedAt?: string;
  stale?: boolean | null;
}

export interface MacroIndicator {
  id: string;
  name: string;
  category: string | null;
  currency: string | null;
  unit: string | null;
  source: string;
  status: 'ok' | 'stale' | 'not_configured' | 'no_data';
  stale: boolean | null;
  latest: { period: string; value: number; knownFrom: string; retrievedAt: string; sourceUrl: string | null } | null;
  previous: { period: string; value: number } | null;
}

export interface MacroOverview {
  asOf: string;
  centralBanks: CentralBank[];
  indicators: MacroIndicator[];
  note: string;
}

export interface NewsItem {
  id: number;
  provider: string;
  source: string;
  headline: string;
  url: string | null;
  publishedAt: string;
  retrievedAt: string;
  category: string | null;
  sourceType: 'official_announcement' | 'reported_news' | string;
  currencies: string[];
  reliability: string | null;
  description: string | null;
  /** AURUM's rule-based reading of the headline wording (lexicon_v1) - not part of the news */
  tone?: HeadlineTone;
}

export interface HeadlineTone {
  kind: 'policy_tone' | 'risk_tone';
  label: 'hawkish' | 'dovish' | 'neutral' | 'positive' | 'negative' | 'unclear';
  terms: string[];
  method: string;
  note: string;
  score?: number;
}

export interface NewsPage {
  items: NewsItem[];
  total: number;
  page: number;
  pageSize: number;
  note: string;
}

export type VolatilityRegime = 'VERY_LOW' | 'LOW' | 'NORMAL' | 'HIGH' | 'VERY_HIGH' | 'UNKNOWN';

export interface TimeframeVolatility {
  timeframe: string;
  candles: number;
  status: string;
  atr: number | null;
  atrPctOfPrice: number | null;
  atrPercentile: number | null;
  percentileSample: number | null;
  realisedVolPct: number | null;
  annualisedVolPct: number | null;
  realisedVolPercentile: number | null;
  expansionRatio: number | null;
  expansionState: string | null;
  abnormalMovement: boolean | null;
  regime: VolatilityRegime;
  reason: string;
}

export interface SessionVolatility {
  meanRange20: number | null;
  sampleSize: number;
  latestRange: number | null;
  latestVsMean: number | null;
  meanRealisedVolPct: number | null;
}

export interface VolatilityReport {
  symbol: string;
  asOf: string;
  timeframes: Record<string, TimeframeVolatility>;
  regime: VolatilityRegime;
  regimeTimeframe: string;
  regimeReason: string;
  daily: { currentRange: number | null; averageDailyRange20: number | null; ratio: number | null; provisional: boolean } | null;
  weekly: { currentRange: number | null; averageWeeklyRange10: number | null; ratio: number | null; provisional: boolean } | null;
  sessions: Record<string, SessionVolatility>;
}

export interface ConditionEvidence {
  source: string;
  fact: string;
}

export interface MarketCondition {
  tag: string;
  confidence: number;
  active: boolean;
  evidence: ConditionEvidence[];
}

export interface Conditions {
  symbol: string;
  asOf: string;
  timeframe: string;
  efficiencyRatio: number | null;
  tags: string[];
  conditions: MarketCondition[];
  summary: string[];
  methodologyVersion: string;
  note: string;
}

export interface ScoreComponent {
  name: string;
  weight: number | null;
  available: boolean;
  value: number | null;
  explanation: string;
}

export interface Scores {
  asOf: string;
  methodologyVersion: string;
  eventRisk: { score: number | null; state: string; reason: string; components: ScoreComponent[] } | null;
  sessionActivity: { score: number | null; components: ScoreComponent[]; explanation: string } | null;
  volatility: { score: number | null; regime: string; explanation: string } | null;
  disclaimer: string;
}

export interface MarketIntelligence {
  symbol: string;
  asOf: string;
  generatedAt: string;
  moduleVersion: string;
  methodologyVersion: string;
  configFingerprint: string;
  sessions: SessionsSnapshot;
  levels: { levels: SessionLevel[]; issues: LevelIssue[] };
  liquidity: SessionLiquidity;
  volatility: VolatilityReport;
  eventRisk: EventRisk;
  conditions: Conditions;
  scores: Scores;
  integrations: Record<string, { available: boolean; reason?: string | null; [k: string]: unknown } | null>;
  dataQuality: { issues: (LevelIssue & { component?: string })[]; calendarCoverage: CalendarCoverage };
  disclaimer: string;
}

export interface IntelligenceStatus {
  moduleVersion: string;
  methodologyVersion: string;
  configFingerprint: string;
  schemaReady: boolean;
  missingTables: string[];
  calendarProviders: CalendarProviderStatus[];
  calendarCoverage: CalendarCoverage | null;
  officialDataEnabled: boolean;
  fredConfigured: boolean;
  instruments: string[];
  tradeExecution: false;
}

export interface CalendarQuery {
  range?: 'today' | 'tomorrow' | 'this_week' | 'next_week';
  currencies?: string[];
  importance?: string[];
  categories?: string[];
  instrument?: string;
  page?: number;
  pageSize?: number;
}
