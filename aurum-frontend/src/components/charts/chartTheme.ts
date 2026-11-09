import { ColorType, CrosshairMode, type DeepPartial, type ChartOptions } from 'lightweight-charts';
import type { Theme } from '@/stores/settingsStore';

/** Canvas charts cannot read CSS variables, so each theme has concrete colours matching the tokens in globals.css. */
export interface ChartPalette {
  background: string;
  text: string;
  grid: string;
  border: string;
  up: string;
  down: string;
  gold: string;
  crosshair: string;
  label: string;
  level: string;
  upVolume: string;
  downVolume: string;
  upHist: string;
  downHist: string;
  series: string[];
}

const LIGHT: ChartPalette = {
  background: '#FFFFFF',
  text: '#6B7280',
  grid: '#F0F1F0',
  border: '#E5E7E6',
  up: '#1A7F52',
  down: '#D64545',
  gold: '#0F4A3A',
  crosshair: '#9CA3AF',
  label: '#0F4A3A',
  level: '#D4D7D5',
  upVolume: 'rgba(26,127,82,0.22)',
  downVolume: 'rgba(214,69,69,0.22)',
  upHist: 'rgba(26,127,82,0.4)',
  downHist: 'rgba(214,69,69,0.4)',
  // Greens from the template's performance chart, then neutrals.
  series: ['#0F4A3A', '#5E9C7E', '#A9D4BC', '#6B7280', '#9CA3AF'],
};

const DARK: ChartPalette = {
  background: '#161A18',
  text: '#98A1AB',
  grid: '#212624',
  border: '#2A302D',
  up: '#3DC984',
  down: '#EF6363',
  gold: '#4FC596',
  crosshair: '#5F6864',
  label: '#0F4A3A',
  level: '#3E4642',
  upVolume: 'rgba(61,201,132,0.25)',
  downVolume: 'rgba(239,99,99,0.25)',
  upHist: 'rgba(61,201,132,0.45)',
  downHist: 'rgba(239,99,99,0.45)',
  series: ['#4FC596', '#6FB592', '#A9D4BC', '#98A1AB', '#7C8580'],
};

export const chartPalette = (theme: Theme): ChartPalette => (theme === 'dark' ? DARK : LIGHT);

/** Light palette; kept for callers that do not follow the theme. */
export const CHART_COLORS = LIGHT;

/** The theme-dependent part of the chart options; applied at creation and again on every theme change. */
export function chartOptions(theme: Theme): DeepPartial<ChartOptions> {
  const c = chartPalette(theme);
  return {
    layout: {
      background: { type: ColorType.Solid, color: c.background },
      textColor: c.text,
      fontFamily: 'Inter, ui-sans-serif, sans-serif',
      fontSize: 11,
    },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: c.crosshair, labelBackgroundColor: c.label },
      horzLine: { color: c.crosshair, labelBackgroundColor: c.label },
    },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border },
  };
}

export const baseChartOptions: DeepPartial<ChartOptions> = {
  ...chartOptions('light'),
  autoSize: true, // ResizeObserver: charts follow sidebar collapse and window resizes
  timeScale: { borderColor: LIGHT.border, timeVisible: true, secondsVisible: false, rightOffset: 6 },
  handleScroll: true,
  handleScale: true,
};
