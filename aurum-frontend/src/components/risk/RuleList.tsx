import { Badge, type Tone } from '@/components/common/Badge';
import type { RuleCheck } from '@/types/risk';
import { SEVERITY_LABEL } from '@/utils/riskLabels';

const TONE: Record<string, Tone> = { HARD_BLOCK: 'down', WARNING: 'warn', INFORMATIONAL: 'info' };

/** Rule results exactly as the engine reported them: id, severity and its own explanation. */
export function RuleList({ title, rules, empty }: { title: string; rules: RuleCheck[]; empty?: string }) {
  if (rules.length === 0 && !empty) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <p className="eyebrow">{title}</p>
      {rules.length === 0 ? (
        <p className="text-xs text-neutral">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rules.map((r) => (
            <li key={`${r.ruleId}-${r.reasonCode}`} className="flex flex-wrap items-start gap-2 text-xs">
              <Badge tone={r.outcome === 'error' ? 'neutral' : TONE[r.severity]}>
                {r.ruleId} · {r.outcome === 'error' ? 'Error' : SEVERITY_LABEL[r.severity]}
              </Badge>
              <span className="min-w-0 flex-1 text-ink">
                <span className="font-medium">{r.name}.</span> {r.message}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
