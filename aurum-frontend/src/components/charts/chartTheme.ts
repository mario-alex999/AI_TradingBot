import { ColorType, CrosshairMode, type DeepPartial, type ChartOptions } from 'lightweight-charts';

export const CHART_COLORS = {
  background: '#FFFFFF',
  text: '#6B7280',
  grid: '#F0F1F0',
  border: '#E5E7E6',
  up: '#1A7F52',
  down: '#D64545',
  gold: '#0F4A3A',
  crosshair: '#9CA3AF',
  // Greens from the template's performance chart, then neutrals.
  series: ['#0F4A3A', '#5E9C7E', '#A9D4BC', '#6B7280', '#9CA3AF'],
};

export const baseChartOptions: DeepPartial<ChartOptions> = {
  autoSize: true, // ResizeObserver: charts follow sidebar collapse and window resizes
  layout: {
    background: { type: ColorType.Solid, color: CHART_COLORS.background },
    textColor: CHART_COLORS.text,
    fontFamily: 'Inter, ui-sans-serif, sans-serif',
    fontSize: 11,
  },
  grid: {
    vertLines: { color: CHART_COLORS.grid },
    horzLines: { color: CHART_COLORS.grid },
  },
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: '#0F4A3A' },
    horzLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: '#0F4A3A' },
  },
  rightPriceScale: { borderColor: CHART_COLORS.border },
  timeScale: { borderColor: CHART_COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 6 },
  handleScroll: true,
  handleScale: true,
};
