import type { Timeframe } from './market';

export type ConnectionState = 'connected' | 'disconnected' | 'collecting' | 'warning' | 'failed' | 'unknown';

/** Endpoint capabilities advertised by the backend in GET /health. */
export type Capability =
  | 'market.collect'
  | 'market.refresh'
  | 'market.export.csv'
  | 'market.export.parquet'
  | 'mt5.check'
  | 'market.specifications.write';

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down';
  version: string | null;
  environment: 'demo' | 'live' | 'unknown';
  mt5: ConnectionState;
  database: ConnectionState;
  collection: ConnectionState;
  brokerName: string | null;
  terminalVersion: string | null;
  capabilities: Capability[];
  checkedAt: string;
}

export interface TimeframeHealth {
  timeframe: Timeframe;
  latestCandle: string | null;
  storedCandles: number | null;
  lastCollection: string | null;
  availability: 'available' | 'partial' | 'missing' | 'unknown';
  quality: ConnectionState;
}

export interface DataQualityReport {
  instrumentsMonitored: string[];
  timeframes: Timeframe[];
  lastSuccessfulCollection: string | null;
  candlesCollected: number | null;
  validationErrors: number | null;
  missingCandles: number | null;
  duplicateCandles: number | null;
  invalidOhlc: number | null;
  unexpectedGaps: number | null;
  collectionErrors: number | null;
  latencyMs: number | null;
  lastValidationResult: 'passed' | 'warning' | 'failed' | null;
  byTimeframe: TimeframeHealth[];
}

export type ValidationStatus = 'passed' | 'warning' | 'failed';

export interface CollectionLog {
  id: string;
  timestamp: string;
  instrument: string;
  timeframe: Timeframe;
  recordsRetrieved: number;
  recordsStored: number;
  validationStatus: ValidationStatus;
  durationMs: number;
  errorMessage: string | null;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export type AgentStatus = 'idle' | 'running' | 'completed' | 'warning' | 'failed' | 'disabled' | 'unknown';

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  status: AgentStatus;
  lastExecution: string | null;
  lastResult: string | null;
  errorCount: number | null;
  configuration: Record<string, string | number | boolean>;
  logs: { timestamp: string; level: 'info' | 'warning' | 'error'; message: string }[];
}

export type NotificationCategory =
  | 'mt5_disconnection'
  | 'database_failure'
  | 'collection_failure'
  | 'invalid_market_data'
  | 'market_movement'
  | 'cot_update'
  | 'signal_generated'
  | 'approval_required'
  | 'risk_limit';

export type Severity = 'info' | 'warning' | 'critical';

export interface AppNotification {
  id: string;
  category: NotificationCategory;
  severity: Severity;
  title: string;
  message: string;
  timestamp: string;
  read: boolean;
}
