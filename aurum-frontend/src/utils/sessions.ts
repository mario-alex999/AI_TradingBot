import type { SessionDefinition } from '@/config/sessions';

/**
 * Session engine. All wall-clock times are interpreted in the session's own
 * IANA zone via Intl, so DST is handled by the platform tz database.
 */

interface ZonedParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
  weekday: number; // 0 = Sunday
}

const partsCache = new Map<string, Intl.DateTimeFormat>();

function formatter(timeZone: string): Intl.DateTimeFormat {
  let f = partsCache.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    partsCache.set(timeZone, f);
  }
  return f;
}

export function zonedParts(instant: number, timeZone: string): ZonedParts {
  const map: Record<string, string> = {};
  for (const p of formatter(timeZone).formatToParts(new Date(instant))) map[p.type] = p.value;
  const year = Number(map.year);
  const month = Number(map.month);
  const day = Number(map.day);
  return {
    year,
    month,
    day,
    hour: Number(map.hour) % 24,
    minute: Number(map.minute),
    second: Number(map.second),
    weekday: new Date(Date.UTC(year, month - 1, day)).getUTCDay(),
  };
}

function offsetMs(instant: number, timeZone: string): number {
  const p = zonedParts(instant, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(instant / 1000) * 1000;
}

/** Converts a wall-clock time in `timeZone` to a UTC instant (ms). */
export function zonedTimeToUtc(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  timeZone: string,
): number {
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  let t = guess - offsetMs(guess, timeZone);
  t = guess - offsetMs(t, timeZone); // second pass settles DST edges
  return t;
}

export interface Interval {
  start: number;
  end: number;
}

function parseHm(hm: string): [number, number] {
  const [h, m] = hm.split(':').map(Number);
  return [h ?? 0, m ?? 0];
}

/** Weekday session intervals from `daysBack` before to `daysAhead` after `now`. */
export function sessionIntervals(def: SessionDefinition, now: number, daysBack = 1, daysAhead = 8): Interval[] {
  const today = zonedParts(now, def.timeZone);
  const [oh, om] = parseHm(def.open);
  const [ch, cm] = parseHm(def.close);
  const out: Interval[] = [];
  for (let k = -daysBack; k <= daysAhead; k++) {
    const d = new Date(Date.UTC(today.year, today.month - 1, today.day + k));
    const wd = d.getUTCDay();
    if (wd === 0 || wd === 6) continue;
    const y = d.getUTCFullYear();
    const mo = d.getUTCMonth() + 1;
    const da = d.getUTCDate();
    const start = zonedTimeToUtc(y, mo, da, oh, om, def.timeZone);
    let end = zonedTimeToUtc(y, mo, da, ch, cm, def.timeZone);
    if (end <= start) end += 86_400_000; // session crossing midnight
    out.push({ start, end });
  }
  return out;
}

export interface SessionState {
  def: SessionDefinition;
  isOpen: boolean;
  isWeekend: boolean;
  current: Interval | null;
  next: Interval | null;
  /** ms until the session closes (if open) or opens (if closed). */
  countdownMs: number;
  intervals: Interval[];
}

export function sessionState(def: SessionDefinition, now: number): SessionState {
  const intervals = sessionIntervals(def, now);
  const current = intervals.find((i) => i.start <= now && now < i.end) ?? null;
  const next = intervals.find((i) => i.start > now) ?? null;
  const wd = zonedParts(now, def.timeZone).weekday;
  return {
    def,
    isOpen: current !== null,
    isWeekend: !current && (wd === 0 || wd === 6),
    current,
    next,
    countdownMs: current ? current.end - now : next ? next.start - now : 0,
    intervals,
  };
}

export function intersect(a: Interval[], b: Interval[]): Interval[] {
  const out: Interval[] = [];
  for (const x of a)
    for (const y of b) {
      const start = Math.max(x.start, y.start);
      const end = Math.min(x.end, y.end);
      if (end > start) out.push({ start, end });
    }
  return out.sort((p, q) => p.start - q.start);
}

/**
 * Spot FX trades from Sunday 17:00 to Friday 17:00 New York time.
 * Derived from the clock only — the broker's own trading hours (from the
 * backend) take precedence once available.
 */
export function forexMarketOpen(now: number): boolean {
  const ny = zonedParts(now, 'America/New_York');
  if (ny.weekday === 6) return false;
  if (ny.weekday === 0) return ny.hour >= 17;
  if (ny.weekday === 5) return ny.hour < 17;
  return true;
}

/** Start of the calendar day containing `now` in `timeZone`, as a UTC instant. */
export function startOfZonedDay(now: number, timeZone: string): number {
  const p = zonedParts(now, timeZone);
  return zonedTimeToUtc(p.year, p.month, p.day, 0, 0, timeZone);
}
