import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import { demoCotReport } from '@/mocks/cot';
import type { CotReport } from '@/types/analysis';
import type { Sourced } from '@/types/api';

export function getCotReport(opts: RequestOptions, instrument: string, weeks = 208): Promise<Sourced<CotReport | null>> {
  return resolveSource(
    opts,
    () => demoCotReport(instrument, weeks),
    (signal) => apiGet<CotReport | null>('/cot/positions', { signal, params: { instrument, weeks } }),
  );
}

export function getCotReportsIndex(opts: RequestOptions): Promise<Sourced<{ contract: string; instrument: string; latestReportDate: string }[]>> {
  return resolveSource(
    opts,
    () => [],
    (signal) => apiGet('/cot/reports', { signal }),
  );
}
