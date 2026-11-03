/** Shared Recharts styling so analytical charts match the AURUM design system. */
export const axisProps = {
  stroke: '#E5E7E6',
  tick: { fill: '#6B7280', fontSize: 11, fontFamily: 'Inter, sans-serif' },
  tickLine: false,
} as const;

export const gridProps = { stroke: '#F0F1F0', vertical: false } as const;

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
  cursor: { stroke: '#9CA3AF', strokeWidth: 1, fill: 'rgba(15,74,58,0.04)' },
} as const;

export const SERIES = {
  // Large speculators are the series the COT screens are about: they take the hue.
  commercial: '#5E9C7E',
  nonCommercial: '#0F4A3A',
  nonReportable: '#A9D4BC',
  oi: '#5E9C7E',
  up: '#1A7F52',
  down: '#D64545',
} as const;

export const legendProps = { wrapperStyle: { fontSize: 12, color: '#4B5563' }, iconType: 'plainline' as const };
