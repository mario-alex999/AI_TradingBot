import axios, { type AxiosRequestConfig } from 'axios';
import { env } from '@/config/env';
import type { ApiErrorKind } from '@/types/api';

/**
 * Central HTTP client for the AURUM AI FastAPI backend.
 *
 * - Base URL comes from VITE_API_BASE_URL.
 * - `withCredentials` lets the backend use httpOnly session cookies, so no
 *   token is ever stored in localStorage.
 * - All failures are normalised into `ApiError` so the UI can distinguish a
 *   disconnected backend from a not-yet-implemented endpoint.
 */
export const apiClient = axios.create({
  baseURL: env.apiBaseUrl,
  timeout: env.requestTimeoutMs,
  withCredentials: true,
  headers: { Accept: 'application/json' },
});

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly endpoint?: string;

  constructor(kind: ApiErrorKind, message: string, status?: number, endpoint?: string) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.endpoint = endpoint;
  }

  /** Whether retrying could plausibly succeed. */
  get retryable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout' || (this.kind === 'http' && (this.status ?? 0) >= 500);
  }
}

interface ErrorBody {
  detail?: unknown;
  message?: unknown;
}

export function toApiError(err: unknown, endpoint?: string): ApiError {
  if (err instanceof ApiError) return err;
  if (axios.isCancel(err)) return new ApiError('cancelled', 'Request cancelled', undefined, endpoint);
  if (axios.isAxiosError<ErrorBody>(err)) {
    if (err.code === 'ECONNABORTED' || err.code === 'ETIMEDOUT')
      return new ApiError('timeout', `Request timed out after ${Math.round(env.requestTimeoutMs / 1000)}s`, undefined, endpoint);
    if (err.code === 'ERR_CANCELED') return new ApiError('cancelled', 'Request cancelled', undefined, endpoint);
    if (!err.response) return new ApiError('network', 'Backend unreachable', undefined, endpoint);
    const { status, data } = err.response;
    if (status === 404 || status === 405 || status === 501)
      return new ApiError('not_implemented', `Endpoint not available on the backend (HTTP ${status})`, status, endpoint);
    const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.message === 'string' ? data.message : err.message;
    return new ApiError('http', detail, status, endpoint);
  }
  return new ApiError('unknown', err instanceof Error ? err.message : 'Unknown error', undefined, endpoint);
}

export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await apiClient.get<T>(url, config);
    return res.data;
  } catch (e) {
    throw toApiError(e, url);
  }
}

export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await apiClient.post<T>(url, body, config);
    return res.data;
  } catch (e) {
    throw toApiError(e, url);
  }
}

export async function apiDownload(url: string, config?: AxiosRequestConfig): Promise<Blob> {
  try {
    const res = await apiClient.get<Blob>(url, { ...config, responseType: 'blob' });
    return res.data;
  } catch (e) {
    throw toApiError(e, url);
  }
}
