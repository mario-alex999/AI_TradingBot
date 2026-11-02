import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import { apiClient } from '@/services/api';

/** Simulates a backend that cannot be reached. */
export const offlineAdapter: AxiosAdapter = (config) =>
  Promise.reject(new AxiosError('Network Error', 'ERR_NETWORK', config as InternalAxiosRequestConfig));

type Handler = (config: InternalAxiosRequestConfig) => { status: number; data: unknown };

/** Routes requests by "METHOD /path" to handlers; anything else returns 404. */
export function mockApi(routes: Record<string, Handler | unknown>): void {
  apiClient.defaults.adapter = (config) => {
    const key = `${(config.method ?? 'get').toUpperCase()} ${config.url}`;
    const route = routes[key];
    const res = route === undefined ? { status: 404, data: { detail: 'Not Found' } } : typeof route === 'function' ? (route as Handler)(config) : { status: 200, data: route };
    const response = { data: res.data, status: res.status, statusText: String(res.status), headers: {}, config };
    if (res.status >= 400)
      return Promise.reject(new AxiosError(`Request failed with status code ${res.status}`, 'ERR_BAD_RESPONSE', config, null, response));
    return Promise.resolve(response);
  };
}
