import { useEffect, useState } from 'react';

/** Current time, ticking every `intervalMs`. A pure client-side clock. */
export function useClock(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
