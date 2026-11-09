import { apiGet, apiPost } from './api';

/**
 * Module 07 sign-in (/api/v1/auth). The password goes to the backend once, over the request body, and is never stored
 * in the browser. The backend answers with an httpOnly session cookie (invisible to scripts) and a CSRF token that is
 * kept in memory only.
 */
export interface ServerUser {
  id: string;
  username: string;
  displayName: string;
  role: 'admin' | 'trader' | 'reviewer' | 'observer' | 'service';
  kind: string;
  permissions: string[];
  accountScope: string[] | null;
}

export interface LoginResponse {
  user: ServerUser;
  csrfToken: string;
  expiresAt: string;
  idleTimeoutMinutes: number;
}

export interface AuthStatus {
  ready: boolean;
  hasUsers: boolean;
  /** Set when there is nothing to sign in to yet (no tables, or no users) and how to fix it. */
  message?: string | null;
}

export const getAuthStatus = (signal?: AbortSignal) => apiGet<AuthStatus>('/auth/status', { signal });
export const login = (username: string, password: string) => apiPost<LoginResponse>('/auth/login', { username, password });
export const logout = () => apiPost<unknown>('/auth/logout');
export const me = () => apiGet<{ user: ServerUser; csrfToken: string }>('/auth/me');
