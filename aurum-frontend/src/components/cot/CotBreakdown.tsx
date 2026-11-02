import { Card, CardHeader } from '@/components/common/Card';
import { formatInteger } from '@/utils/format';
import { cn } from '@/utils/cn';
import type { CotDerived } from '@/utils/cot';

function Net({ v }: { v: number }) {
  return <span className={cn('num font-semibold', v > 0 ? 'text-up' : v < 0 ? 'text-down-text' : 'text-ink')}>{v > 0 ? '+' : ''}{formatInteger(v)}</span>;
}

export function CotBreakdown({ latest }: { latest: CotDerived | null }) {
  const rows = latest
    ? [
        { label: 'Commercial', long: latest.commercialLong, short: latest.commercialShort, net: latest.commercialNet, chg: latest.commercialNetChange },
        { label: 'Non-commercial', long: latest.nonCommercialLong, short: latest.nonCommercialShort, net: latest.nonCommercialNet, chg: latest.nonCommercialNetChange },
        { label: 'Non-reportable', long: latest.nonReportableLong, short: latest.nonReportableShort, net: latest.nonReportableNet, chg: null },
      ]
    : [];
  return (
    <Card>
      <CardHeader title="Positioning breakdown" subtitle={latest ? `Report date ${latest.reportDate}` : 'No report available'} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <caption className="sr-only">Positioning breakdown</caption>
          <thead>
            <tr className="border-b border-line text-2xs uppercase tracking-wider text-neutral">
              <th scope="col" className="px-4 py-2.5 text-left font-semibold">Category</th>
              <th scope="col" className="px-4 py-2.5 text-right font-semibold">Long</th>
              <th scope="col" className="px-4 py-2.5 text-right font-semibold">Short</th>
              <th scope="col" className="px-4 py-2.5 text-right font-semibold">Net</th>
              <th scope="col" className="px-4 py-2.5 text-right font-semibold">Net Δ w/w</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-xs text-neutral">
                  --
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.label} className="border-b border-line-soft last:border-0">
                  <th scope="row" className="px-4 py-2.5 text-left font-medium text-ink">{r.label}</th>
                  <td className="num px-4 py-2.5 text-right text-ink-muted">{formatInteger(r.long)}</td>
                  <td className="num px-4 py-2.5 text-right text-ink-muted">{formatInteger(r.short)}</td>
                  <td className="px-4 py-2.5 text-right"><Net v={r.net} /></td>
                  <td className="px-4 py-2.5 text-right">{r.chg == null ? <span className="text-neutral">--</span> : <Net v={r.chg} />}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
