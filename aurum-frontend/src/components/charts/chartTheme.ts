/**
 * Lightweight Charts styling. Colours are getters over the active theme's palette, read when a chart is
 * created; AppLayout remounts the page on a theme change, so charts are recreated with the new colours.
 */
import { ColorType, CrosshairMode, type DeepPartial, type ChartOptions } from 'lightweight-charts';
import { chartPalette } from '@/theme/theme';

export const CHART_COLORS = {
  get background() {
    return chartPalette().background;
  },
  get text() {
    return chartPalette().text;
  },
  get grid() {
    return chartPalette().grid;
  },
  get border() {
    return chartPalette().border;
  },
  get up() {
    return chartPalette().up;
  },
  get down() {
    return chartPalette().down;
  },
  get gold() {
    return chartPalette().accent;
  },
  get crosshair() {
    return chartPalette().crosshair;
  },
  get reference() {
    return chartPalette().reference;
  },
  get upSoft() {
    return chartPalette().upSoft;
  },
  get downSoft() {
    return chartPalette().downSoft;
  },
  get upHist() {
    return chartPalette().upHist;
  },
  get downHist() {
    return chartPalette().downHist;
  },
  // Greens from the template's performance chart, then neutrals.
  get series(): readonly string[] {
    return chartPalette().series;
  },
};

export const baseChartOptions: DeepPartial<ChartOptions> = {
  autoSize: true, // ResizeObserver: charts follow sidebar collapse and window resizes
  get layout() {
    return {
      background: { type: ColorType.Solid, color: CHART_COLORS.background },
      textColor: CHART_COLORS.text,
      fontFamily: 'Inter, ui-sans-serif, sans-serif',
      fontSize: 11,
    };
  },
  get grid() {
    return { vertLines: { color: CHART_COLORS.grid }, horzLines: { color: CHART_COLORS.grid } };
  },
  get crosshair() {
    const label = chartPalette().tooltipBg;
    return {
      mode: CrosshairMode.Normal,
      vertLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: label },
      horzLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: label },
    };
  },
  get rightPriceScale() {
    return { borderColor: CHART_COLORS.border };
  },
  get timeScale() {
    return { borderColor: CHART_COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 6 };
  },
  handleScroll: true,
  handleScale: true,
};
