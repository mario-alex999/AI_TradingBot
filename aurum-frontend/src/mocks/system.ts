/**
 * ─────────────────────────────  DEMO DATA  ─────────────────────────────
 * Agent registry and sample notifications for UI preview. No agent is
 * reported as running: status stays "idle"/"disabled" until the backend says
 * otherwise.
 */
import type { AgentInfo, AppNotification } from '@/types/system';

const AGENTS: [string, string, string][] = [
  ['market-data', 'Market Data Agent', 'Collects and validates MT5 OHLCV candles for configured instruments and timeframes.'],
  ['cot', 'COT Intelligence Agent', 'Ingests weekly CFTC Commitments of Traders reports and computes positioning metrics.'],
  ['structure', 'Market Structure Agent', 'Identifies swing structure, BOS and CHoCH across timeframes.'],
  ['smc', 'ICT/SMC Analysis Agent', 'Detects order blocks, fair value gaps and liquidity pools for chart overlays.'],
  ['sessions', 'Trading Session Agent', 'Tracks session ranges, overlaps and liquidity conditions.'],
  ['risk', 'Risk Management Agent', 'Enforces backend risk limits and monitors exposure and drawdown.'],
  ['strategy', 'Strategy Evaluation Agent', 'Backtests and scores strategies on historical data.'],
  ['decision', 'AI Decision Agent', 'Combines evidence into trade candidates for human review. Never executes trades.'],
];

export function demoAgents(): AgentInfo[] {
  return AGENTS.map(([id, name, description], i) => ({
    id,
    name,
    description,
    status: i === 0 ? 'idle' : 'disabled',
    lastExecution: null,
    lastResult: null,
    errorCount: null,
    configuration: { schedule: i === 0 ? 'every 15m (demo)' : 'not configured', enabled: i === 0 },
    logs: [],
  }));
}

const now = () => Date.now();

export function demoNotifications(): AppNotification[] {
  const t = now();
  const mk = (
    id: string,
    category: AppNotification['category'],
    severity: AppNotification['severity'],
    title: string,
    message: string,
    minsAgo: number,
    read = false,
  ): AppNotification => ({ id, category, severity, title, message, timestamp: new Date(t - minsAgo * 60_000).toISOString(), read });
  return [
    mk('n1', 'mt5_disconnection', 'critical', 'MT5 terminal disconnected', 'DEMO: sample alert — the MT5 bridge stopped responding.', 4),
    mk('n2', 'collection_failure', 'warning', 'Collection failed for US30 M5', 'DEMO: sample alert — 0 of 288 candles stored.', 37),
    mk('n3', 'invalid_market_data', 'warning', 'Invalid OHLC records', 'DEMO: sample alert — 2 candles where high < close.', 92),
    mk('n4', 'cot_update', 'info', 'COT report published', 'DEMO: sample alert — weekly COT data available.', 300, true),
    mk('n5', 'database_failure', 'critical', 'Database write timeout', 'DEMO: sample alert — candle insert exceeded 5s.', 1440, true),
  ];
}
