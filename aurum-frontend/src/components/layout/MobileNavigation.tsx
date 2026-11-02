import { useEffect } from 'react';
import { X } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { Logo } from '@/components/common/Logo';
import { useUiStore } from '@/stores/uiStore';
import { NavList } from './NavList';

/** Slide-in navigation drawer for tablet and mobile widths. */
export function MobileNavigation() {
  const { mobileNavOpen, setMobileNavOpen } = useUiStore();
  const { pathname } = useLocation();

  useEffect(() => setMobileNavOpen(false), [pathname, setMobileNavOpen]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMobileNavOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileNavOpen, setMobileNavOpen]);

  if (!mobileNavOpen) return null;
  return (
    <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
      <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-black/60" onClick={() => setMobileNavOpen(false)} />
      <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] animate-fade-in flex-col border-r border-line bg-base-raised">
        <div className="flex h-14 items-center justify-between border-b border-line px-4">
          <Logo />
          <button type="button" aria-label="Close navigation" className="text-neutral hover:text-ink" onClick={() => setMobileNavOpen(false)}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-4">
          <NavList />
        </div>
      </div>
    </div>
  );
}
