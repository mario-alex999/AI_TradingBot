import { useChartStore } from '@/stores/chartStore';

export function useChartSelection() {
  const { instrument, timeframe, setInstrument, setTimeframe } = useChartStore();
  return { instrument, timeframe, setInstrument, setTimeframe };
}
