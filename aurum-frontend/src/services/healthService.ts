import { z } from 'zod';
import { apiGet, ApiError } from './api';
import type { HealthStatus } from '@/types/system';

const connection = z.enum(['connected', 'disconnected', 'collecting', 'warning', 'failed', 'unknown']).catch('unknown');

/** Tolerant schema: missing fields become "unknown" instead of guessed values. */
export const healthSchema = z.object({
  status: z.enum(['ok', 'degraded', 'down']).catch('degraded'),
  version: z.string().nullable().catch(null).default(null),
  environment: z.enum(['demo', 'live', 'unknown']).catch('unknown').default('unknown'),
  mt5: connection.default('unknown'),
  database: connection.default('unknown'),
  collection: connection.default('unknown'),
  broker_name: z.string().nullable().catch(null).default(null),
  terminal_version: z.string().nullable().catch(null).default(null),
  capabilities: z.array(z.string()).catch([]).default([]),
});

/** GET /health — always queried against the real backend, even in mock mode. */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthStatus> {
  const raw = await apiGet<unknown>('/health', { signal });
  const parsed = healthSchema.safeParse(raw);
  if (!parsed.success) throw new ApiError('validation', 'Unexpected /health response shape', undefined, '/health');
  const h = parsed.data;
  return {
    status: h.status,
    version: h.version,
    environment: h.environment,
    mt5: h.mt5,
    database: h.database,
    collection: h.collection,
    brokerName: h.broker_name,
    terminalVersion: h.terminal_version,
    capabilities: h.capabilities as HealthStatus['capabilities'],
    checkedAt: new Date().toISOString(),
  };
}
