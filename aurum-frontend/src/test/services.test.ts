import { describe, expect, it } from 'vitest';
import { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { ApiError, toApiError } from '@/services/api';
import { getQuotes } from '@/services/marketService';
import { getAccountSummary } from '@/services/tradingService';
import { getSignals } from '@/services/analysisService';
import { fetchHealth } from '@/services/healthService';
import { mockApi } from './mockApi';

const cfg = { headers: {} } as InternalAxiosRequestConfig;
const httpError = (status: number) =>
  new AxiosError('fail', 'ERR_BAD_RESPONSE', cfg, null, { status, data: { detail: `boom ${status}` }, statusText: '', headers: {}, config: cfg });

describe('API error normalisation', () => {
  it('distinguishes network, timeout, missing endpoint and server errors', () => {
    expect(toApiError(new AxiosError('x', 'ERR_NETWORK', cfg)).kind).toBe('network');
    expect(toApiError(new AxiosError('x', 'ECONNABORTED', cfg)).kind).toBe('timeout');
    expect(toApiError(httpError(404)).kind).toBe('not_implemented');
    const e = toApiError(httpError(503));
    expect(e.kind).toBe('http');
    expect(e.message).toBe('boom 503');
    expect(e.retryable).toBe(true);
    expect(toApiError(httpError(400)).retryable).toBe(false);
  });
});

describe('data source switching', () => {
  it('returns labelled demo data in mock mode without touching the network', async () => {
    const r = await getQuotes({ mock: true }, ['EURUSD']);
    expect(r.source).toBe('demo');
    expect(r.data[0]!.symbol).toBe('EURUSD');
  });

  it('never fabricates account figures or signals in mock mode', async () => {
    expect((await getAccountSummary({ mock: true })).data).toBeNull();
    expect((await getSignals({ mock: true })).data).toEqual([]);
  });

  it('uses the live backend when mock mode is off and tags it as live', async () => {
    mockApi({ 'GET /market/quotes': [{ symbol: 'EURUSD', bid: 1.1 }] });
    const r = await getQuotes({ mock: false }, ['EURUSD']);
    expect(r.source).toBe('live');
    expect(r.data).toEqual([{ symbol: 'EURUSD', bid: 1.1 }]);
  });

  it('surfaces backend failures instead of silently falling back to demo data', async () => {
    await expect(getQuotes({ mock: false })).rejects.toMatchObject({ kind: 'network' });
    await expect(getQuotes({ mock: false })).rejects.toBeInstanceOf(ApiError);
  });
});

describe('health parsing', () => {
  it('fills unknown fields with "unknown" rather than guessing', async () => {
    mockApi({ 'GET /health': { status: 'ok', mt5: 'connected' } });
    const h = await fetchHealth();
    expect(h.mt5).toBe('connected');
    expect(h.database).toBe('unknown');
    expect(h.environment).toBe('unknown');
    expect(h.capabilities).toEqual([]);
  });
});
