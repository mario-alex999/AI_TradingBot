/**
 * Shared Recharts styling so analytical charts match the AURUM design system.
 * Colours are getters over the active theme's palette (read when a chart renders), so spreading
 * `{...axisProps}` always yields the current theme's colours.
 */
import { chartPalette } from '@/theme/theme';

const FONT = 'Inter, sans-serif';

export const axisProps = {
  get stroke() {
    return chartPalette().border;
  },
  get tick() {
    return { fill: chartPalette().text, fontSize: 11, fontFamily: FONT };
  },
  tickLine: false as const,
};

export const gridProps = {
  get stroke() {
    return chartPalette().grid;
  },
  vertical: false as const,
};

export const tooltipProps = {
  get contentStyle() {
    const p = chartPalette();
    return { background: p.tooltipBg, border: 'none', borderRadius: 8, fontSize: 12, fontFamily: FONT, color: p.tooltipText };
  },
  get labelStyle() {
    return { color: chartPalette().tooltipLabel };
  },
  get itemStyle() {
    return { color: chartPalette().tooltipText };
  },
  get cursor() {
    const p = chartPalette();
    return { stroke: p.crosshair, strokeWidth: 1, fill: p.cursorFill };
  },
};

export const SERIES = {
  // Large speculators are the series the COT screens are about: they take the hue.
  get commercial() {
    return chartPalette().green2;
  },
  get nonCommercial() {
    return chartPalette().green1;
  },
  get nonReportable() {
    return chartPalette().green3;
  },
  get oi() {
    return chartPalette().green2;
  },
  get up() {
    return chartPalette().up;
  },
  get down() {
    return chartPalette().down;
  },
  get accent() {
    return chartPalette().accent;
  },
  get reference() {
    return chartPalette().reference;
  },
};

export const legendProps = {
  get wrapperStyle() {
    return { fontSize: 12, color: chartPalette().legend };
  },
  iconType: 'plainline' as const,
};
