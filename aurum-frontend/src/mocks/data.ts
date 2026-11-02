/**
 * ─────────────────────────────  DEMO DATA  ─────────────────────────────
 * Synthetic Data Centre records for UI development. Not collected from MT5.
 */
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import type { CollectionLog, DataQualityReport, Paginated } from '@/types/system';
import type { InstrumentSpecification } from '@/types/market';
import type { LogQuery } from '@/services/dataService';
import { mulberry32 } from './random';
import { demoProfile } from './market';

export function demoSpecifications(): InstrumentSpecification[] {
  const contract: Record<string, number> = { EURUSD: 100_000, GBPUSD: 100_000, XAUUSD: 100, US30: 1 };
  return DEFAULT_INSTRUMENTS.map((i) => {
    const digits = demoProfile(i.symbol).digits;
    const point = 10 ** -digits;
    return {
      symbol: i.symbol,
      brokerSymbol: i.symbol,
      assetClass: i.assetClass,
      enabled: i.enabled,
      digits,
      point,
      contractSize: contract[i.symbol] ?? null,
      tickSize: point,
      tickValue: null,
    };
  });
}

const BASE_TIME = Date.UTC(2026, 0, 5, 6, 0, 0);

function buildLogs(): CollectionLog[] {
  const rand = mulberry32(7);
  const logs: CollectionLog[] = [];
  let t = BASE_TIME;
  for (let i = 0; i < 96; i++) {
    const inst = DEFAULT_INSTRUMENTS[i % DEFAULT_INSTRUMENTS.length]!.symbol;
    const tf = TIMEFRAMES[Math.floor(i / 4) % TIMEFRAMES.length]!;
    const retrieved = 200 + Math.floor(rand() * 300);
    const roll = rand();
    const status = roll > 0.93 ? 'failed' : roll > 0.82 ? 'warning' : 'passed';
    logs.push({
      id: `demo-${i + 1}`,
      timestamp: new Date(t).toISOString(),
      instrument: inst,
      timeframe: tf,
      recordsRetrieved: status === 'failed' ? 0 : retrieved,
      recordsStored: status === 'failed' ? 0 : status === 'warning' ? retrieved - Math.ceil(rand() * 4) : retrieved,
      validationStatus: status,
      durationMs: Math.round(120 + rand() * 900),
      errorMessage:
        status === 'failed' ? 'DEMO: terminal not responding' : status === 'warning' ? 'DEMO: gaps detected in range' : null,
    });
    t += 15 * 60_000;
  }
  return logs.reverse();
}

const LOGS = buildLogs();

export function demoCollectionLogs(q: LogQuery): Paginated<CollectionLog> {
  const s = q.search?.trim().toLowerCase();
  const filtered = LOGS.filter(
    (l) =>
      (!q.instrument || l.instrument === q.instrument) &&
      (!q.timeframe || l.timeframe === q.timeframe) &&
      (!q.status || l.validationStatus === q.status) &&
      (!s ||
        l.instrument.toLowerCase().includes(s) ||
        l.timeframe.toLowerCase().includes(s) ||
        (l.errorMessage ?? '').toLowerCase().includes(s)),
  );
  const start = (q.page - 1) * q.pageSize;
  return { items: filtered.slice(start, start + q.pageSize), total: filtered.length, page: q.page, pageSize: q.pageSize };
}

export function demoDataQuality(): DataQualityReport {
  const last = LOGS[0]?.timestamp ?? null;
  return {
    instrumentsMonitored: DEFAULT_INSTRUMENTS.map((i) => i.symbol),
    timeframes: [...TIMEFRAMES],
    lastSuccessfulCollection: LOGS.find((l) => l.validationStatus !== 'failed')?.timestamp ?? null,
    candlesCollected: LOGS.reduce((s, l) => s + l.recordsStored, 0),
    validationErrors: LOGS.filter((l) => l.validationStatus === 'failed').length,
    missingCandles: 14,
    duplicateCandles: 0,
    invalidOhlc: 2,
    unexpectedGaps: 3,
    collectionErrors: LOGS.filter((l) => l.validationStatus === 'failed').length,
    latencyMs: 342,
    lastValidationResult: 'warning',
    byTimeframe: TIMEFRAMES.map((tf, i) => ({
      timeframe: tf,
      latestCandle: last,
      storedCandles: [1250, 4800, 18200, 64000, 182000][i] ?? null,
      lastCollection: last,
      availability: i === 4 ? 'partial' : 'available',
      quality: i === 4 ? 'warning' : 'connected',
    })),
  };
}
