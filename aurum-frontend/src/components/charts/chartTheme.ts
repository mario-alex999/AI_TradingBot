import { ColorType, CrosshairMode, type DeepPartial, type ChartOptions } from 'lightweight-charts';

export const CHART_COLORS = {
  background: '#121215',
  text: '#8A8C92',
  grid: '#1E1E22',
  border: '#26262A',
  up: '#16C784',
  down: '#EA3943',
  gold: '#D6AD60',
  crosshair: '#8A8C92',
  // Gold leads; the rest are neutrals so the price stays the loudest thing.
  series: ['#D6AD60', '#F2F2F3', '#8A8C92', '#B4B5BA', '#6E7076'],
};

export const baseChartOptions: DeepPartial<ChartOptions> = {
  autoSize: true, // ResizeObserver: charts follow sidebar collapse and window resizes
  layout: {
    background: { type: ColorType.Solid, color: CHART_COLORS.background },
    textColor: CHART_COLORS.text,
    fontFamily: '"JetBrains Mono", ui-monospace, monospace',
    fontSize: 11,
  },
  grid: {
    vertLines: { color: CHART_COLORS.grid },
    horzLines: { color: CHART_COLORS.grid },
  },
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: '#212023' },
    horzLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: '#212023' },
  },
  rightPriceScale: { borderColor: CHART_COLORS.border },
  timeScale: { borderColor: CHART_COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 6 },
  handleScroll: true,
  handleScale: true,
};
