import { apiGet, apiPost } from './api';

/**
 * Per-person MT5 link (/api/v1/mt5). After signing in, a person authorises their own DEMO account; the backend logs a
 * dedicated terminal in to it and mirrors it read-only. The broker password is sent once and never stored or returned.
 */
export type Mt5LinkStatus = 'CONNECTING' | 'CONNECTED' | 'DISCONNECTING' | 'DISCONNECTED' | 'FAILED' | 'LOST';

export interface Mt5Connection {
  id: string;
  login: number;
  server: string;
  slot: number;
  status: Mt5LinkStatus;
  company: string | null;
  currency: string | null;
  leverage: number | null;
  tradeMode: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  lastSync: { deals?: number; positions?: number; open?: number; cashFlows?: number } | null;
  createdAt: string;
  connectedAt: string | null;
  lastSyncAt: string | null;
  disconnectedAt: string | null;
  disconnectReason: string | null;
}

export interface Mt5ConnectionResponse {
  connection: Mt5Connection | null;
  accountScope?: string[] | null;
}

export interface Mt5ServerStatus {
  enabled: boolean;
  slots: number;
  readySlots: number;
  freeSlots: number;
}

export const getMt5Connection = (signal?: AbortSignal) => apiGet<Mt5ConnectionResponse>('/mt5/connection', { signal });
export const getMt5Status = (signal?: AbortSignal) => apiGet<Mt5ServerStatus>('/mt5/status', { signal });
export const connectMt5 = (login: string, password: string, server: string) =>
  apiPost<Mt5ConnectionResponse>('/mt5/connect', { login, password, server });
export const disconnectMt5 = () => apiPost<Mt5ConnectionResponse>('/mt5/disconnect');

export const MT5_ACTIVE: Mt5LinkStatus[] = ['CONNECTING', 'CONNECTED', 'DISCONNECTING'];
