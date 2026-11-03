/** Shared Recharts styling so analytical charts match the AURUM design system. */
export const axisProps = {
  stroke: '#26262A',
  tick: { fill: '#8A8C92', fontSize: 11, fontFamily: '"JetBrains Mono", monospace' },
  tickLine: false,
} as const;

export const gridProps = { stroke: '#232327', vertical: false } as const;

export const tooltipProps = {
  contentStyle: {
    background: '#212023',
    border: '1px solid #3A3A3E',
    borderRadius: 6,
    fontSize: 12,
    fontFamily: '"JetBrains Mono", monospace',
    color: '#F2F2F3',
  },
  labelStyle: { color: '#B4B5BA' },
  cursor: { stroke: '#8A8C92', strokeWidth: 1, fill: 'rgba(255,255,255,0.03)' },
} as const;

export const SERIES = {
  // Large speculators are the series the COT screens are about: they take the hue.
  commercial: '#F2F2F3',
  nonCommercial: '#D6AD60',
  nonReportable: '#8A8C92',
  oi: '#B4B5BA',
  up: '#16C784',
  down: '#EA3943',
} as const;

export const legendProps = { wrapperStyle: { fontSize: 12, color: '#B4B5BA' }, iconType: 'plainline' as const };
