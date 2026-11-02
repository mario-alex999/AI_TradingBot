import type { Quote } from '@/types/market';

/**
 * Server-Sent Events client for future real-time quote streaming.
 * Expected event: `event: quote`, `data: <Quote JSON>`.
 * Returns an unsubscribe function. Reconnection is handled by EventSource.
 */
export function subscribeToQuotes(
  url: string,
  onQuote: (q: Quote) => void,
  onStatus?: (s: 'open' | 'error') => void,
): () => void {
  if (typeof EventSource === 'undefined') return () => undefined;
  const es = new EventSource(url, { withCredentials: true });
  es.addEventListener('quote', (ev) => {
    try {
      onQuote(JSON.parse((ev as MessageEvent<string>).data) as Quote);
    } catch {
      /* ignore malformed frames */
    }
  });
  es.onopen = () => onStatus?.('open');
  es.onerror = () => onStatus?.('error');
  return () => es.close();
}
