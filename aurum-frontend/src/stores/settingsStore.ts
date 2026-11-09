import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { env } from '@/config/env';
import type { NotificationCategory } from '@/types/system';
import type { Timeframe } from '@/types/market';
import { DEFAULT_SESSIONS, type SessionDefinition } from '@/config/sessions';

export type DateFormat = 'dd MMM yyyy' | 'yyyy-MM-dd' | 'MM/dd/yyyy';
export type Theme = 'light' | 'dark';
export type NumberLocale = 'en-US' | 'en-GB' | 'de-DE' | 'fr-FR';

/** Non-sensitive UI preferences only. Never store credentials here. */
export interface SettingsState {
  mockMode: boolean;
  displayTimezone: string;
  dateFormat: DateFormat;
  numberLocale: NumberLocale;
  defaultInstrument: string;
  defaultTimeframe: Timeframe;
  showSpreadInPips: boolean;
  mutedCategories: NotificationCategory[];
  sessions: SessionDefinition[];
  /** Colour theme. Applied as the `dark` class on <html> (see useApplyTheme). */
  theme: Theme;
  /** Demo mode only: animate the demo quotes. */
  demoTicks: boolean;
  setMockMode: (v: boolean) => void;
  setDisplayTimezone: (tz: string) => void;
  update: (patch: Partial<Omit<SettingsState, 'update' | 'setMockMode' | 'setDisplayTimezone'>>) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      mockMode: env.mockDataDefault,
      displayTimezone: 'UTC',
      dateFormat: 'dd MMM yyyy',
      numberLocale: 'en-US',
      defaultInstrument: 'EURUSD',
      defaultTimeframe: 'H1',
      showSpreadInPips: false,
      mutedCategories: [],
      sessions: DEFAULT_SESSIONS,
      demoTicks: true,
      theme: 'light',
      setMockMode: (mockMode) => set({ mockMode }),
      setDisplayTimezone: (displayTimezone) => set({ displayTimezone }),
      update: (patch) => set(patch),
    }),
    { name: 'aurum.settings', version: 1, storage: createJSONStorage(() => localStorage) },
  ),
);
