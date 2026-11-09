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

/**
 * CSRF token for the signed-in session (Module 07 sign-in). Held in memory only and sent as X-CSRF-Token on every
 * state-changing request; the session itself is an httpOnly cookie that scripts never see. After a reload the token is
 * fetched again from /auth/me.
 */
let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}
apiClient.interceptors.request.use((config) => {
  const method = (config.method ?? 'get').toLowerCase();
  if (csrfToken && !['get', 'head', 'options'].includes(method)) config.headers.set('X-CSRF-Token', csrfToken);
  return config;
});

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly endpoint?: string;
  /** Machine-readable code from a structured `{ error: { code } }` body (e.g. Module 06 `WEAKENING_NOT_ACKNOWLEDGED`). */
  readonly code?: string;
  /** Structured details, e.g. the list of failed approval checks returned with APPROVAL_CHECKS_FAILED. */
  readonly details?: unknown;

  constructor(kind: ApiErrorKind, message: string, status?: number, endpoint?: string, code?: string, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.endpoint = endpoint;
    this.code = code;
    this.details = details;
  }

  /** Whether retrying could plausibly succeed. */
  get retryable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout' || (this.kind === 'http' && (this.status ?? 0) >= 500);
  }
}

interface ErrorBody {
  detail?: unknown;
  message?: unknown;
  error?: { code?: unknown; details?: unknown };
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
    const code = typeof data?.error?.code === 'string' ? data.error.code : undefined;
    // A structured 404 (e.g. NOT_FOUND for an approval) is a real answer, not a missing endpoint.
    if ((status === 404 && !code) || status === 405 || status === 501)
      return new ApiError('not_implemented', `Endpoint not available on the backend (HTTP ${status})`, status, endpoint);
    const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.message === 'string' ? data.message : err.message;
    return new ApiError('http', detail, status, endpoint, code, data?.error?.details);
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

export async function apiPut<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await apiClient.put<T>(url, body, config);
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
