/**
 * Theme: "light" | "dark" | "system" (follows the OS). The choice lives in the settings store; the resolved
 * theme toggles the `dark` class on <html>, which switches every CSS colour token (see globals.css).
 *
 * Charts draw with JS colours (SVG attributes, canvas), so they read `chartPalette()`. AppLayout remounts the
 * page when the theme changes, so charts are rebuilt with the new palette.
 */
import { useEffect, useSyncExternalStore } from 'react';
import { useSettingsStore } from '@/stores/settingsStore';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(DARK_QUERY).matches;
}

function subscribeSystem(cb: () => void): () => void {
  const mql = typeof window !== 'undefined' ? window.matchMedia?.(DARK_QUERY) : undefined;
  mql?.addEventListener?.('change', cb);
  return () => mql?.removeEventListener?.('change', cb);
}

export function resolveTheme(pref: ThemePreference, systemDark: boolean): ResolvedTheme {
  return pref === 'system' ? (systemDark ? 'dark' : 'light') : pref;
}

const PALETTES = {
  light: {
    dark: false,
    background: '#FFFFFF',
    text: '#6B7280',
    grid: '#F0F1F0',
    border: '#E5E7E6',
    reference: '#D4D7D5',
    crosshair: '#9CA3AF',
    accent: '#0F4A3A',
    up: '#1A7F52',
    down: '#D64545',
    upSoft: 'rgba(26,127,82,0.22)',
    downSoft: 'rgba(214,69,69,0.22)',
    upHist: 'rgba(26,127,82,0.4)',
    downHist: 'rgba(214,69,69,0.4)',
    legend: '#4B5563',
    tooltipBg: '#0F4A3A',
    tooltipText: '#FFFFFF',
    tooltipLabel: 'rgba(255,255,255,0.75)',
    cursorFill: 'rgba(15,74,58,0.04)',
    // Greens from the template's performance chart, then neutrals.
    series: ['#0F4A3A', '#5E9C7E', '#A9D4BC', '#6B7280', '#9CA3AF'],
    green1: '#0F4A3A',
    green2: '#5E9C7E',
    green3: '#A9D4BC',
    grey1: '#6B7280',
    grey2: '#9CA3AF',
  },
  dark: {
    dark: true,
    background: '#141917',
    text: '#8F979E',
    grid: '#1E2421',
    border: '#262D2A',
    reference: '#3A433F',
    crosshair: '#6B7280',
    accent: '#3FB98A',
    up: '#3FB97E',
    down: '#F06A6A',
    upSoft: 'rgba(63,185,126,0.28)',
    downSoft: 'rgba(240,106,106,0.28)',
    upHist: 'rgba(63,185,126,0.45)',
    downHist: 'rgba(240,106,106,0.45)',
    legend: '#A9B1AD',
    tooltipBg: '#1F2623',
    tooltipText: '#E6EAE8',
    tooltipLabel: 'rgba(230,234,232,0.7)',
    cursorFill: 'rgba(63,185,138,0.08)',
    series: ['#3FB98A', '#7CC4A1', '#B7E2CB', '#8F979E', '#5D666C'],
    green1: '#3FB98A',
    green2: '#7CC4A1',
    green3: '#B7E2CB',
    grey1: '#8F979E',
    grey2: '#5D666C',
  },
} as const;

export type ChartPalette = (typeof PALETTES)[ResolvedTheme];

let active: ResolvedTheme = 'light';

/** Colours for charts in the active theme. Read at render time, never cached at module load. */
export function chartPalette(): ChartPalette {
  return PALETTES[active];
}

export function setActiveTheme(t: ResolvedTheme): void {
  active = t;
}

/** The resolved theme. Also records it for chartPalette() during render, before any chart renders. */
export function useResolvedTheme(): ResolvedTheme {
  const pref = useSettingsStore((s) => s.theme);
  const systemDark = useSyncExternalStore(subscribeSystem, systemPrefersDark, () => false);
  const resolved = resolveTheme(pref, systemDark);
  setActiveTheme(resolved);
  return resolved;
}

/** Applies the resolved theme to <html> (class + color-scheme) and the browser's theme-color. Use once, at the root. */
export function useApplyTheme(): ResolvedTheme {
  const resolved = useResolvedTheme();
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', resolved === 'dark');
    root.style.colorScheme = resolved;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#0C100F' : '#EEEFEE');
  }, [resolved]);
  return resolved;
}
