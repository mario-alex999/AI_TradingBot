import { afterEach, describe, expect, it } from 'vitest';
import { chartPalette, resolveTheme, setActiveTheme } from '@/theme/theme';
import { axisProps, SERIES, tooltipProps } from '@/components/analytics/chartStyles';
import { baseChartOptions } from '@/components/charts/chartTheme';
import { CATEGORY_COLORS } from '@/components/cot/cotFormat';

afterEach(() => setActiveTheme('light'));

describe('theme', () => {
  it('resolves the preference, following the OS for "system"', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });

  it('chart styles read the active palette when used, not when imported', () => {
    setActiveTheme('light');
    const light = { ...axisProps, tooltip: tooltipProps.contentStyle.background, bg: baseChartOptions.layout?.background };
    setActiveTheme('dark');
    const dark = { ...axisProps, tooltip: tooltipProps.contentStyle.background, bg: baseChartOptions.layout?.background };
    expect(light.stroke).not.toBe(dark.stroke);
    expect(light.tick.fill).not.toBe(dark.tick.fill);
    expect(light.tooltip).not.toBe(dark.tooltip);
    expect(dark.bg).toEqual(expect.objectContaining({ color: chartPalette().background }));
    expect(SERIES.nonCommercial).toBe(chartPalette().green1);
    expect(CATEGORY_COLORS.managed_money).toBe(chartPalette().green1);
    expect(Object.keys(CATEGORY_COLORS)).toContain('commercial');
  });
});
