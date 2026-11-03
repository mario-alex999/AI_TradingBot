import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import { SkeletonRows } from '@/components/common/Skeleton';
import { useRealtimeQuotes } from '@/hooks/useRealtimeQuotes';
import { MobileNavigation } from './MobileNavigation';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';

export function AppLayout() {
  useRealtimeQuotes();
  return (
    <div className="flex min-h-screen bg-base">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-gold focus:px-3 focus:py-2 focus:text-base">
        Skip to content
      </a>
      <Sidebar />
      <MobileNavigation />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main id="main" className="mx-auto w-full max-w-[1680px] flex-1 px-4 pb-8 pt-5 lg:px-8 lg:pt-6">
          <Suspense fallback={<SkeletonRows rows={8} />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
