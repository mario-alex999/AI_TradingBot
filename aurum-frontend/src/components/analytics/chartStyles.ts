/** Shared Recharts styling so analytical charts match the AURUM design system. Colours are CSS variables, so the charts
 * follow the light/dark theme. The tooltip stays on the dark-green feature colour in both themes. */
export const axisProps = {
  stroke: 'rgb(var(--c-line))',
  tick: { fill: 'rgb(var(--c-neutral))', fontSize: 11, fontFamily: 'Inter, sans-serif' },
  tickLine: false,
} as const;

export const gridProps = { stroke: 'rgb(var(--c-line-soft))', vertical: false } as const;

export const tooltipProps = {
  contentStyle: {
    background: '#0F4A3A',
    border: 'none',
    borderRadius: 8,
    fontSize: 12,
    fontFamily: 'Inter, sans-serif',
    color: '#FFFFFF',
  },
  labelStyle: { color: 'rgba(255,255,255,0.75)' },
  cursor: { stroke: 'rgb(var(--c-line-control))', strokeWidth: 1, fill: 'rgb(var(--c-gold) / 0.06)' },
} as const;

export const SERIES = {
  // Large speculators are the series the COT screens are about: they take the hue.
  commercial: 'rgb(var(--c-series-2))',
  nonCommercial: 'rgb(var(--c-gold))',
  nonReportable: 'rgb(var(--c-series-3))',
  oi: 'rgb(var(--c-series-2))',
  up: 'rgb(var(--c-up))',
  down: 'rgb(var(--c-down))',
} as const;

export const legendProps = { wrapperStyle: { fontSize: 12, color: 'rgb(var(--c-ink-muted))' }, iconType: 'plainline' as const };
