/**
 * Trading sessions are defined in their home market's IANA time zone so that
 * daylight-saving transitions are resolved by the browser's tz database —
 * never by hard-coded UTC offsets.
 */
export interface SessionDefinition {
  id: 'asia' | 'london' | 'newyork';
  name: string;
  timeZone: string;
  open: string; // HH:mm local to timeZone
  close: string; // HH:mm local to timeZone
}

export const DEFAULT_SESSIONS: SessionDefinition[] = [
  { id: 'asia', name: 'Asian Session', timeZone: 'Asia/Tokyo', open: '09:00', close: '18:00' },
  { id: 'london', name: 'London Session', timeZone: 'Europe/London', open: '08:00', close: '17:00' },
  { id: 'newyork', name: 'New York Session', timeZone: 'America/New_York', open: '08:00', close: '17:00' },
];

export const DISPLAY_TIMEZONES = [
  'UTC',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Chicago',
  'Asia/Dubai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Africa/Johannesburg',
] as const;
