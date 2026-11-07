import { lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/layout/AppLayout';
import { RequireAuth, RequirePermission } from '@/components/layout/ProtectedRoute';
import { Toaster } from '@/components/common/Toaster';
import DashboardPage from '@/pages/DashboardPage';
import LoginPage from '@/pages/LoginPage';

// Secondary pages are code-split for fast first paint.
const MarketOverviewPage = lazy(() => import('@/pages/MarketOverviewPage'));
const InstrumentDetailPage = lazy(() => import('@/pages/InstrumentDetailPage'));
const ChartsPage = lazy(() => import('@/pages/ChartsPage'));
const MarketStructurePage = lazy(() => import('@/pages/MarketStructurePage'));
const InstitutionalIntelligencePage = lazy(() => import('@/pages/InstitutionalIntelligencePage'));
const COTAnalysisPage = lazy(() => import('@/pages/COTAnalysisPage'));
const TradingSessionsPage = lazy(() => import('@/pages/TradingSessionsPage'));
const MarketIntelligencePage = lazy(() => import('@/pages/MarketIntelligencePage'));
const AIEnginePage = lazy(() => import('@/pages/AIEnginePage'));
const AISignalsPage = lazy(() => import('@/pages/AISignalsPage'));
const TradeManagementPage = lazy(() => import('@/pages/TradeManagementPage'));
const RiskManagementPage = lazy(() => import('@/pages/RiskManagementPage'));
const BacktestingPage = lazy(() => import('@/pages/BacktestingPage'));
const PerformancePage = lazy(() => import('@/pages/PerformancePage'));
const DataCentrePage = lazy(() => import('@/pages/DataCentrePage'));
const AgentCentrePage = lazy(() => import('@/pages/AgentCentrePage'));
const NotificationsPage = lazy(() => import('@/pages/NotificationsPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'));

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="market" element={<MarketOverviewPage />} />
          <Route path="market/:symbol" element={<InstrumentDetailPage />} />
          <Route path="charts" element={<ChartsPage />} />
          <Route path="structure" element={<MarketStructurePage />} />
          <Route path="institutional" element={<InstitutionalIntelligencePage />} />
          <Route path="cot" element={<COTAnalysisPage />} />
          <Route path="sessions" element={<TradingSessionsPage />} />
          <Route path="intelligence" element={<MarketIntelligencePage />} />
          <Route path="ai-engine" element={<RequirePermission permission="view:trading"><AIEnginePage /></RequirePermission>} />
          <Route path="signals" element={<RequirePermission permission="view:trading"><AISignalsPage /></RequirePermission>} />
          <Route path="trades" element={<RequirePermission permission="view:trading"><TradeManagementPage /></RequirePermission>} />
          <Route path="risk" element={<RequirePermission permission="view:risk"><RiskManagementPage /></RequirePermission>} />
          <Route path="backtesting" element={<RequirePermission permission="view:trading"><BacktestingPage /></RequirePermission>} />
          <Route path="performance" element={<RequirePermission permission="view:trading"><PerformancePage /></RequirePermission>} />
          <Route path="data" element={<DataCentrePage />} />
          <Route path="agents" element={<RequirePermission permission="view:system"><AgentCentrePage /></RequirePermission>} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="dashboard" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
      <Toaster />
    </>
  );
}
