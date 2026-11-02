import { apiDownload, apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import { demoHistory, demoInstruments, demoStatus } from '@/mocks/cotDemo';
import type {
  CotAnalysis,
  CotDivergence,
  CotExportKind,
  CotHistory,
  CotIngestionLog,
  CotInstrument,
  CotPaginated,
  CotQualityIssue,
  CotReportDetail,
  CotReportRow,
  CotReportType,
  CotSeverity,
  CotStatus,
} from '@/types/cot';
import type { Sourced } from '@/types/api';

/** COT Institutional Intelligence Engine (Module 02) - /api/v1/cot/*. */

export function getCotStatus(opts: RequestOptions, probe = false): Promise<Sourced<CotStatus>> {
  return resolveSource(opts, demoStatus, (signal) => apiGet<CotStatus>('/cot/status', { signal, params: { probe } }));
}

export function getCotInstruments(opts: RequestOptions): Promise<Sourced<CotInstrument[]>> {
  return resolveSource(opts, demoInstruments, (signal) => apiGet<CotInstrument[]>('/cot/instruments', { signal }));
}

export function getCotHistory(opts: RequestOptions, instrument: string, reportType: CotReportType): Promise<Sourced<CotHistory | null>> {
  return resolveSource(
    opts,
    () => demoHistory(instrument, reportType),
    (signal) => apiGet<CotHistory>(`/cot/history/${encodeURIComponent(instrument)}`, { signal, params: { reportType } }),
  );
}

/** Interpretations come only from the backend: the demo layer deliberately returns null. */
export function getCotAnalysis(opts: RequestOptions, instrument: string, asOf?: string): Promise<Sourced<CotAnalysis | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<CotAnalysis>(`/cot/analysis/${encodeURIComponent(instrument)}`, { signal, params: { asOf: asOf || undefined } }),
  );
}

export function getCotDivergence(opts: RequestOptions, instrument: string, reportType: CotReportType, category?: string): Promise<Sourced<CotDivergence | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<CotDivergence>(`/cot/divergence/${encodeURIComponent(instrument)}`, { signal, params: { reportType, category } }),
  );
}

export interface CotQualityQuery {
  page: number;
  pageSize: number;
  severity?: CotSeverity | '';
  instrument?: string;
}

const emptyPage = <T,>(page: number, pageSize: number): CotPaginated<T> => ({ items: [], total: 0, page, pageSize });

export function getCotQuality(opts: RequestOptions, q: CotQualityQuery): Promise<Sourced<CotPaginated<CotQualityIssue>>> {
  return resolveSource(
    opts,
    () => emptyPage<CotQualityIssue>(q.page, q.pageSize),
    (signal) =>
      apiGet('/cot/quality', { signal, params: { page: q.page, pageSize: q.pageSize, severity: q.severity || undefined, instrument: q.instrument || undefined } }),
  );
}

export function getCotLogs(opts: RequestOptions, page = 1, pageSize = 10): Promise<Sourced<CotPaginated<CotIngestionLog>>> {
  return resolveSource(opts, () => emptyPage<CotIngestionLog>(page, pageSize), (signal) => apiGet('/cot/logs', { signal, params: { page, pageSize } }));
}

export function getCotReports(
  opts: RequestOptions,
  q: { instrument: string; reportType: CotReportType; page: number; pageSize: number; start?: string; end?: string },
): Promise<Sourced<CotPaginated<CotReportRow>>> {
  return resolveSource(
    opts,
    () => emptyPage<CotReportRow>(q.page, q.pageSize),
    (signal) => apiGet('/cot/reports', { signal, params: { ...q, start: q.start || undefined, end: q.end || undefined } }),
  );
}

export function getCotReportDetail(opts: RequestOptions, id: number): Promise<Sourced<CotReportDetail | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<CotReportDetail>(`/cot/reports/${id}`, { signal }));
}

/** CSV export - live backend only (exports never contain demo data). */
export function exportCot(kind: CotExportKind, instrument?: string): Promise<Blob> {
  return apiDownload(`/cot/export/${kind}`, { params: { instrument } });
}
