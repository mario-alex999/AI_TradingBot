/** GET /intelligence/news-impact (Module 04, method news_impact_v1). */
export type Direction = 'up' | 'down' | 'mixed' | null;
export type CurrencyEffect = 'bullish' | 'bearish' | 'neutral' | null;
export type ScenarioKey = 'above' | 'inline' | 'below';

export interface ReactionHistory {
  samples: number;
  avgMovePct: number;
  upPct: number;
  /** Share of past releases where the move matched the rule (null when the rule has no direction). */
  followedRulePct: number | null;
  minutes: number;
}

export interface ScenarioInstrument {
  instrument: string;
  direction: Direction;
  explanation: string;
  history: ReactionHistory | null;
}

export interface Scenario {
  key: ScenarioKey;
  label: string;
  currencyEffect: CurrencyEffect;
  instruments: ScenarioInstrument[];
}

export interface EventOutcome {
  scenario: ScenarioKey | null;
  surprise: number | null;
  surprisePct: number | null;
  currencyEffect: CurrencyEffect;
  instruments: { instrument: string; expected: Direction; movePct: number | null; minutesMeasured: number; followedRule: boolean | null }[];
  note?: string;
}

export interface ImpactEvent {
  id: number;
  name: string;
  currency: string | null;
  country: string | null;
  category: string | null;
  subcategory: string | null;
  importance: 'low' | 'medium' | 'high' | null;
  scheduledAt: string;
  timePrecision: string | null;
  status: string | null;
  released: boolean;
  actual: number | string | null;
  consensus: number | string | null;
  previous: number | string | null;
  unit: string | null;
  sourceUrl: string | null;
  instruments: { instrument: string; why: string | null }[];
  expectation: { vsPrevious: 'higher' | 'lower' | 'unchanged'; text: string } | null;
  polarity: 1 | -1 | null;
  rule: string;
  scenarios: Scenario[];
  outcome: EventOutcome | null;
  historySamples: number;
}

export interface ImpactHeadline {
  id: number;
  headline: string;
  source: string;
  url: string;
  publishedAt: string;
  category: string | null;
  currencies: string[];
  tone: { kind: 'policy_tone' | 'risk_tone'; label: string; terms: string[] };
  effect: { currencies: string[]; bias: 'bullish' | 'bearish' } | null;
  instruments: { instrument: string; direction: Direction; explanation: string }[];
}

export interface NewsImpactReport {
  generatedAt: string;
  method: string;
  reactionMinutes: number;
  disclaimer: string;
  window: { start: string; end: string };
  importance: string[];
  nextEvent: ImpactEvent | null;
  events: ImpactEvent[];
  headlines: ImpactHeadline[];
}
