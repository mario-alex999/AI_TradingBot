/** Shape of GET /agents/learning (camelCased by the backend). */
export type LearningAgentStatus = 'discovering' | 'trialling' | 'replicated' | 'terminated' | 'capped';

export interface LearningStats {
  trades: number;
  wins: number;
  net: number;
  expectancy: number;
  profitFactor: number | null;
  winRate: number;
  tStat: number | null;
}

export interface LearningTrial {
  number: number;
  pattern: [string, string][];
  passed: boolean;
  inconclusive: boolean;
  stats: LearningStats;
  tickets: string[];
  at: string;
}

export interface LearningEvent {
  at: string;
  kind: string;
  message: string;
}

export interface LearningAgent {
  id: string;
  generation: number;
  parent: string | null;
  status: LearningAgentStatus;
  createdAt: string;
  endedAt: string | null;
  pattern: [string, string][] | null;
  discoveryStats: LearningStats | null;
  trialProgress: { matched: number; seen: number };
  failedTrials: number;
  trials: LearningTrial[];
  events: LearningEvent[];
}

export interface LearningLineage {
  id: string;
  status: 'running' | LearningAgentStatus | 'empty';
  createdAt: string;
  config: {
    discoveryWindow: number;
    minPatternTrades: number;
    minProfitFactor: number;
    trialTrades: number;
    trialMinProfitFactor: number;
    maxFailedTrials: number;
    maxGenerations: number;
  };
  tradesObserved: number;
  lastCloseTime: string | null;
  activeAgent: string | null;
  agents: LearningAgent[];
  readOnly: boolean;
}
