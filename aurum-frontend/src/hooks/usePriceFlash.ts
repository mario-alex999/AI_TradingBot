import { useEffect, useRef, useState } from 'react';

/** Returns 'up' | 'down' for ~700ms after `value` changes, so a price can flash its direction. */
export function usePriceFlash(value: number): 'up' | 'down' | null {
  const prev = useRef(value);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);
  useEffect(() => {
    if (value === prev.current) return;
    setFlash(value > prev.current ? 'up' : 'down');
    prev.current = value;
    const t = window.setTimeout(() => setFlash(null), 700);
    return () => window.clearTimeout(t);
  }, [value]);
  return flash;
}
