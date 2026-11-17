import { Suspense } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { SkeletonRows } from '@/components/common/Skeleton';
import { useRealtimeQuotes } from '@/hooks/useRealtimeQuotes';
import { useDemoTicker } from '@/hooks/useDemoTicker';
import { CommandPalette } from './CommandPalette';
import { ShortcutsDialog } from './ShortcutsDialog';
import { useGlobalShortcuts } from '@/hooks/useGlobalShortcuts';
import { MobileNavigation } from './MobileNavigation';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { useResolvedTheme } from '@/theme/theme';
import { Mt5Prompt } from '@/components/mt5/Mt5Prompt';

export function AppLayout() {
  useRealtimeQuotes();
  useDemoTicker();
  useGlobalShortcuts();
  // Charts draw with JS colours: remount the page on a theme change so they redraw (query cache is kept).
  const theme = useResolvedTheme();
  const { pathname } = useLocation();
  return (
    <div className="flex min-h-screen bg-base">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-gold focus:px-3 focus:py-2 focus:text-white">
        Skip to content
      </a>
      <Sidebar />
      <MobileNavigation />
      <CommandPalette />
      <ShortcutsDialog />
      <Mt5Prompt />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main id="main" className="mx-auto w-full max-w-[1680px] flex-1 px-4 pb-8 pt-5 lg:px-8 lg:pt-6">
          {/* A page that fails to render shows a message here; the menu and other pages keep working. */}
          <ErrorBoundary what="this page" resetKey={pathname}>
            <Suspense fallback={<SkeletonRows rows={8} />}>
              <Outlet key={theme} />
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
