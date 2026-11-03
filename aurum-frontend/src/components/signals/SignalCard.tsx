import { ArrowDownRight, ArrowUpRight } from 'lucide-react';
import { Card } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { SIGNAL_STATUS } from '@/config/signals';
import { Button } from '@/components/common/Button';
import { formatDateTime, formatNumber } from '@/utils/format';
import type { TradeSignal } from '@/types/trading';


export function SignalCard({ s, digits = 5 }: { s: TradeSignal; digits?: number }) {
  const long = s.direction === 'long';
  const st = SIGNAL_STATUS[s.status];
  const rows: [string, string][] = [
    ['Entry zone', `${formatNumber(s.entryZone[0], digits)} – ${formatNumber(s.entryZone[1], digits)}`],
    ['Stop loss', formatNumber(s.stopLoss, digits)],
    ['Take profit', formatNumber(s.takeProfit, digits)],
    ['Risk / reward', `1 : ${s.riskReward.toFixed(2)}`],
    ['Strategy', s.strategy],
    ['Timeframe', s.timeframe],
    ['Market condition', s.marketCondition],
  ];
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={long ? 'text-up' : 'text-down-text'}>{long ? <ArrowUpRight className="h-5 w-5" aria-hidden /> : <ArrowDownRight className="h-5 w-5" aria-hidden />}</span>
          <div>
            <p className="num text-base font-semibold text-ink">{s.instrument}</p>
            <p className={long ? 'text-xs font-medium capitalize text-up' : 'text-xs font-medium capitalize text-down-text'}>{s.direction}</p>
          </div>
        </div>
        <Badge tone={st.tone}>{st.label}</Badge>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-neutral">{k}</dt>
            <dd className="num text-right text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      <div>
        <div className="flex justify-between text-2xs">
          <span className="text-neutral">Confidence score</span>
          <span className="num text-ink">{s.confidence}/100</span>
        </div>
        <div className="mt-1 h-1.5 rounded-full bg-base-elevated">
          <div className="h-1.5 rounded-full bg-gold" style={{ width: `${Math.max(0, Math.min(100, s.confidence))}%` }} />
        </div>
        <p className="mt-1 text-xs text-neutral">Model confidence — not a probability of success.</p>
      </div>
      {s.evidence.length > 0 && (
        <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
          {s.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex items-center justify-between gap-2 border-t border-line-soft pt-2">
        <span className="num text-2xs text-neutral">{formatDateTime(s.timestamp)} UTC</span>
        <div className="flex gap-1.5">
          <Button size="sm" variant="ghost" disabled disabledReason="Approval workflow requires backend authorisation (read-only release)">
            Reject
          </Button>
          <Button size="sm" disabled disabledReason="Approval workflow requires backend authorisation (read-only release)">
            Approve
          </Button>
        </div>
      </div>
    </Card>
  );
}
