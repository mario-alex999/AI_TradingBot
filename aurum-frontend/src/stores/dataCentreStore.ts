import { create } from 'zustand';
import type { ValidationStatus } from '@/types/system';
import type { Timeframe } from '@/types/market';

/** Log filters shared across Data Centre panels, so a click on a quality tile or a timeframe drills into the logs. */
interface DataCentreFilters {
  timeframe: Timeframe | '';
  status: ValidationStatus | '';
  setTimeframe: (t: Timeframe | '') => void;
  setStatus: (s: ValidationStatus | '') => void;
}

export const useDataCentreStore = create<DataCentreFilters>()((set) => ({
  timeframe: '',
  status: '',
  setTimeframe: (timeframe) => set({ timeframe }),
  setStatus: (status) => set({ status }),
}));

export function scrollToLogs(): void {
  const el = document.getElementById('collection-logs');
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
