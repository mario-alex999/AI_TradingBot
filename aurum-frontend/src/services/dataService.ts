import { apiClient, apiDownload, apiGet, apiPost, toApiError } from './api';
import { resolveSource, type RequestOptions } from './source';
import { demoCollectionLogs, demoDataQuality, demoSpecifications } from '@/mocks/data';
import type { CollectionLog, DataQualityReport, Paginated } from '@/types/system';
import type { InstrumentSpecification, Timeframe } from '@/types/market';
import type { Sourced } from '@/types/api';

export function getSpecifications(opts: RequestOptions): Promise<Sourced<InstrumentSpecification[]>> {
  return resolveSource(opts, demoSpecifications, (signal) =>
    apiGet<InstrumentSpecification[]>('/market/specifications', { signal }),
  );
}

export function getDataQuality(opts: RequestOptions): Promise<Sourced<DataQualityReport>> {
  return resolveSource(opts, demoDataQuality, (signal) => apiGet<DataQualityReport>('/market/data-quality', { signal }));
}

export interface LogQuery {
  page: number;
  pageSize: number;
  search?: string;
  instrument?: string;
  timeframe?: Timeframe | '';
  status?: CollectionLog['validationStatus'] | '';
}

export function getCollectionLogs(opts: RequestOptions, q: LogQuery): Promise<Sourced<Paginated<CollectionLog>>> {
  return resolveSource(
    opts,
    () => demoCollectionLogs(q),
    (signal) =>
      apiGet<Paginated<CollectionLog>>('/market/collection-logs', {
        signal,
        params: {
          page: q.page,
          page_size: q.pageSize,
          search: q.search || undefined,
          instrument: q.instrument || undefined,
          timeframe: q.timeframe || undefined,
          status: q.status || undefined,
        },
      }),
  );
}

export interface CollectRequest {
  instruments?: string[];
  timeframes?: Timeframe[];
}

/** POST /market/collect — only ever invoked against the live backend. */
export function triggerCollection(body: CollectRequest): Promise<{ jobId: string; status: string }> {
  return apiPost('/market/collect', body);
}

export function exportMarketData(format: 'csv' | 'parquet', params: { symbol?: string; timeframe?: Timeframe }): Promise<Blob> {
  return apiDownload('/market/export', { params: { format, ...params } });
}

export type SpecificationUpdate = Pick<InstrumentSpecification, 'brokerSymbol' | 'enabled'>;

/** PUT /market/specifications/{symbol} — validated and authorised by the backend. */
export async function updateSpecification(symbol: string, body: SpecificationUpdate): Promise<InstrumentSpecification> {
  try {
    const res = await apiClient.put<InstrumentSpecification>(`/market/specifications/${encodeURIComponent(symbol)}`, body);
    return res.data;
  } catch (e) {
    throw toApiError(e, `/market/specifications/${symbol}`);
  }
}
