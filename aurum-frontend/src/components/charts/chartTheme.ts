import { ColorType, CrosshairMode, type DeepPartial, type ChartOptions } from 'lightweight-charts';

export const CHART_COLORS = {
  background: '#0B121F',
  text: '#8B98AA',
  grid: '#172235',
  border: '#273449',
  up: '#16C784',
  down: '#EA3943',
  gold: '#D6AD60',
  crosshair: '#A7B2C2',
  series: ['#D6AD60', '#5B9BEA', '#B58CF0', '#E3A13B', '#4FD1C5'],
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
    vertLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: '#19263A' },
    horzLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: '#19263A' },
  },
  rightPriceScale: { borderColor: CHART_COLORS.border },
  timeScale: { borderColor: CHART_COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 6 },
  handleScroll: true,
  handleScale: true,
};
