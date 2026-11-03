import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { GO_TO } from '@/config/shortcuts';
import { useUiStore } from '@/stores/uiStore';

const isTyping = (t: EventTarget | null) => {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));
};

/** App-wide shortcuts: G then a letter to navigate, "/" search, "[" sidebar, "?" help. Ignored while typing. */
export function useGlobalShortcuts(): void {
  const navigate = useNavigate();
  const pendingG = useRef<number | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      const ui = useUiStore.getState();
      if (ui.paletteOpen) return;
      if (pendingG.current !== null) {
        window.clearTimeout(pendingG.current);
        pendingG.current = null;
        const target = GO_TO.find((g) => g.key === e.key.toLowerCase());
        if (target) {
          e.preventDefault();
          navigate(target.path);
        }
        return;
      }
      if (e.key === 'g' || e.key === 'G') {
        pendingG.current = window.setTimeout(() => (pendingG.current = null), 1200);
      } else if (e.key === '?') {
        e.preventDefault();
        ui.setShortcutsOpen(!ui.shortcutsOpen);
      } else if (e.key === '/') {
        e.preventDefault();
        ui.setPaletteOpen(true);
      } else if (e.key === '[') {
        ui.toggleSidebar();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);
}
