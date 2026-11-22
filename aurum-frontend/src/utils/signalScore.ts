import { outcomeState } from '@/config/signals';
import type { OutcomeState, TradeSignal } from '@/types/trading';

/** Grades that make a candidate a recommended setup (Module 05 decision.min_grades). */
export const RECOMMENDED_GRADES = ['A', 'B'];

/**
 * A recommended setup: grade A or B, and not rejected by a safety gate or the AI reviewer. Lower grades and rejected
 * candidates are research only; counting them in the win rate made the results look like trades that were never advised.
 * A person's own rejection does not change whether it was recommended.
 */
export function isRecommended(s: TradeSignal): boolean {
  if (!s.grade || !RECOMMENDED_GRADES.includes(s.grade)) return false;
  const by = s.rejectedBy ?? '';
  return !(by.startsWith('gate:') || by === 'ai_reviewer');
}

const IDEA_WINDOW_MS = 2 * 60 * 60 * 1000;

/**
 * One entry per trade idea. Before 22 Nov 2026 the engine stored the same idea several times: several strategies on one
 * zone, or the next run republishing it with slightly moved levels. Signals on the same instrument and direction, with
 * overlapping entry zones, raised within 2 hours of the first, are one idea; the first one published represents it.
 */
export function distinctIdeas(signals: TradeSignal[]): TradeSignal[] {
  const ordered = [...signals].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const ideas: TradeSignal[] = [];
  for (const s of ordered) {
    const [lo, hi] = s.entryZone[0] <= s.entryZone[1] ? s.entryZone : [s.entryZone[1], s.entryZone[0]];
    const same = ideas.find((i) => {
      const [ilo, ihi] = i.entryZone[0] <= i.entryZone[1] ? i.entryZone : [i.entryZone[1], i.entryZone[0]];
      return (
        i.instrument === s.instrument &&
        i.direction === s.direction &&
        lo <= ihi &&
        ilo <= hi &&
        Date.parse(s.timestamp) - Date.parse(i.timestamp) <= IDEA_WINDOW_MS
      );
    });
    if (!same) ideas.push(s);
  }
  return ideas;
}

export type ScoreScope = 'recommended' | 'all';

export interface Scoreboard {
  counts: Partial<Record<OutcomeState, number>>;
  wins: number;
  losses: number;
  winRate: number | null;
  /** Ideas counted, and the signals they came from. */
  ideas: number;
  signals: number;
  totalR: number;
}

export function scoreboard(all: TradeSignal[], scope: ScoreScope): Scoreboard {
  const pool = scope === 'recommended' ? all.filter(isRecommended) : all;
  const ideas = distinctIdeas(pool);
  const counts: Partial<Record<OutcomeState, number>> = {};
  let totalR = 0;
  for (const s of ideas) {
    const st = outcomeState(s.outcome);
    counts[st] = (counts[st] ?? 0) + 1;
    if (s.outcome?.filled && s.outcome.rMultiple != null && st !== 'running') totalR += s.outcome.rMultiple;
  }
  const wins = counts.take_profit ?? 0;
  const losses = (counts.stop_loss ?? 0) + (counts.ambiguous ?? 0);
  return {
    counts,
    wins,
    losses,
    winRate: wins + losses > 0 ? Math.round((wins / (wins + losses)) * 100) : null,
    ideas: ideas.length,
    signals: pool.length,
    totalR: Math.round(totalR * 100) / 100,
  };
}
