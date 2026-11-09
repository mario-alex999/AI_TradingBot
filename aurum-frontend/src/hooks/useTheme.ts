import { useEffect } from 'react';
import { useSettingsStore, type Theme } from '@/stores/settingsStore';

/** Browser chrome colour (mobile address bar) for each theme: the canvas colour. */
const THEME_COLOR: Record<Theme, string> = { light: '#EEEFEE', dark: '#0B0E0D' };

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
}

/** Keeps the `dark` class on <html> in step with the saved setting. index.html applies it before first paint. */
export function useApplyTheme() {
  const theme = useSettingsStore((s) => s.theme);
  useEffect(() => applyTheme(theme), [theme]);
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useSettingsStore((s) => s.theme);
  const update = useSettingsStore((s) => s.update);
  return { theme, toggle: () => update({ theme: theme === 'dark' ? 'light' : 'dark' }) };
}
