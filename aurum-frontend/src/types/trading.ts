import type { Timeframe } from './market';

export interface AccountSummary {
  environment: 'demo' | 'live';
  currency: string;
  balance: number;
  equity: number;
  freeMargin: number;
  marginLevel: number | null;
  openPositions: number;
  pendingApprovals: number;
  updatedAt: string;
}

export type SignalDirection = 'long' | 'short';
export type SignalStatus = 'new' | 'under_review' | 'approved' | 'rejected' | 'expired' | 'executed';

export interface TradeSignal {
  id: string;
  instrument: string;
  direction: SignalDirection;
  entryZone: [number, number];
  stopLoss: number;
  takeProfit: number;
  riskReward: number;
  strategy: string;
  timeframe: Timeframe;
  marketCondition: string;
  /** Model confidence score (0–100). NOT a probability of success. */
  confidence: number;
  evidence: string[];
  timestamp: string;
  status: SignalStatus;
  /** The limit entry the risk-reward and the outcome use: the middle of the entry zone. */
  entryPrice?: number;
  /** What the signal did in the market afterwards (hypothetical: AURUM places no trades). */
  outcome?: SignalOutcome | null;
}

/**
 * pending: not checked yet · waiting: entry not reached yet · running: triggered, no exit yet · take_profit / stop_loss:
 * exit reached · ambiguous: target and stop inside one candle (counted as a loss) · closed_at_limit: triggered and closed
 * after the holding limit · not_triggered: expired before the entry was reached.
 */
export type OutcomeState =
  | 'pending'
  | 'waiting'
  | 'running'
  | 'take_profit'
  | 'stop_loss'
  | 'ambiguous'
  | 'closed_at_limit'
  | 'not_triggered';

export interface SignalOutcome {
  state?: OutcomeState;
  touched: boolean;
  filled: boolean;
  /** Backend label: tp | sl | expired | ambiguous | open (null before the first check). */
  result: string | null;
  rMultiple: number | null;
  mfeR: number | null;
  maeR: number | null;
  /** When price reached the entry (open time of that M5 candle). */
  filledAt?: string | null;
  resolvedAt: string | null;
  exitPrice?: number | null;
}

export interface Position {
  ticket: string;
  instrument: string;
  side: 'buy' | 'sell';
  volume: number;
  entryPrice: number;
  stopLoss: number | null;
  takeProfit: number | null;
  currentPrice: number | null;
  floatingPnl: number | null;
  openedAt: string;
  closedAt?: string | null;
  closePrice?: number | null;
  realisedPnl?: number | null;
  /** Pending orders only: buy_limit | sell_limit | buy_stop | sell_stop | buy_stop_limit | sell_stop_limit. */
  orderType?: string | null;
  expiresAt?: string | null;
  kind: 'position' | 'pending' | 'closed';
}

export interface PerformanceMetrics {
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  profitFactor: number;
  averageProfit: number;
  averageLoss: number;
  expectancy: number;
  maxDrawdownPct: number;
  sharpe: number | null;
}

export interface PerformanceReport {
  kind: 'live' | 'backtest';
  metrics: PerformanceMetrics;
  equityCurve: { time: string; equity: number }[];
  monthly: { month: string; returnPct: number }[];
  byInstrument: { name: string; pnl: number }[];
  bySession: { name: string; pnl: number }[];
  byStrategy: { name: string; pnl: number }[];
}
