export type DataSource = 'live' | 'demo';

/**
 * Every dataset the UI renders carries its origin so DEMO DATA can never be
 * mistaken for backend data.
 */
export interface Sourced<T> {
  data: T;
  source: DataSource;
  fetchedAt: string;
}

export type ApiErrorKind = 'network' | 'timeout' | 'http' | 'not_implemented' | 'cancelled' | 'validation' | 'unknown';
