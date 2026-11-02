/** Shared Recharts styling so analytical charts match the AURUM design system. */
export const axisProps = {
  stroke: '#273449',
  tick: { fill: '#8B98AA', fontSize: 11, fontFamily: '"JetBrains Mono", monospace' },
  tickLine: false,
} as const;

export const gridProps = { stroke: '#1D2A3E', strokeDasharray: '3 3', vertical: false } as const;

export const tooltipProps = {
  contentStyle: {
    background: '#19263A',
    border: '1px solid #273449',
    borderRadius: 8,
    fontSize: 12,
    fontFamily: '"JetBrains Mono", monospace',
    color: '#F1F5F9',
  },
  labelStyle: { color: '#A7B2C2' },
  cursor: { stroke: '#A7B2C2', strokeWidth: 1, fill: 'rgba(255,255,255,0.03)' },
} as const;

export const SERIES = {
  commercial: '#5B9BEA',
  nonCommercial: '#D6AD60',
  nonReportable: '#B58CF0',
  oi: '#4FD1C5',
  up: '#16C784',
  down: '#EA3943',
} as const;

export const legendProps = { wrapperStyle: { fontSize: 11, color: '#A7B2C2' }, iconType: 'plainline' as const };
