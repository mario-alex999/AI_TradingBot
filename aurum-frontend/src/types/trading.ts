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
