import { useMemo } from 'react';
import { useSettingsStore } from '@/stores/settingsStore';
import { intersect, sessionState, type Interval, type SessionState } from '@/utils/sessions';

export interface SessionsSnapshot {
  sessions: SessionState[];
  overlapActive: Interval | null;
  nextOverlap: Interval | null;
  activeLabel: string;
}

/** Derives all session state for `now`. Recomputed once per minute bucket. */
export function useSessions(now: number): SessionsSnapshot {
  const minute = Math.floor(now / 60_000);
  const defs = useSettingsStore((s) => s.sessions);
  return useMemo(() => {
    const t = minute * 60_000 + (now % 60_000);
    const sessions = defs.map((d) => sessionState(d, t));
    const london = sessions.find((s) => s.def.id === 'london');
    const ny = sessions.find((s) => s.def.id === 'newyork');
    const overlaps = london && ny ? intersect(london.intervals, ny.intervals) : [];
    const overlapActive = overlaps.find((o) => o.start <= t && t < o.end) ?? null;
    const nextOverlap = overlaps.find((o) => o.start > t) ?? null;
    const open = sessions.filter((s) => s.isOpen).map((s) => s.def.name.replace(' Session', ''));
    return {
      sessions,
      overlapActive,
      nextOverlap,
      activeLabel: open.length ? open.join(' + ') : 'No major session open',
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minute, defs]);
}
