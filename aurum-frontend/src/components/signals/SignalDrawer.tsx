import { useEffect, useState, type ReactNode } from 'react';
import { ShieldAlert } from 'lucide-react';
import { Badge, type Tone } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { Drawer } from '@/components/common/Drawer';
import { QueryState } from '@/components/common/QueryState';
import { SIGNAL_STATUS } from '@/config/signals';
import { useAdminGate, useDossier, useReviewSignal } from '@/hooks/useDecision';
import { useAuthStore } from '@/stores/authStore';
import { formatDateTime, formatNumber } from '@/utils/format';
import { humanize } from '@/utils/labels';
import type { GateRow, ReviewAction, SignalDossier } from '@/types/decision';
import { SignalChart } from './SignalChart';

const GATE: Record<GateRow['result'], { label: string; tone: Tone }> = {
  pass: { label: 'Pass', tone: 'up' },
  warn: { label: 'Warning', tone: 'warn' },
  fail: { label: 'Failed', tone: 'down' },
};

const VERDICT: Record<string, Tone> = { agree: 'up', caution: 'warn', reject: 'down' };

const ACTION_LABEL: Record<ReviewAction, string> = { approve: 'Approve', reject: 'Reject', reopen: 'Reopen' };

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-line-soft pt-3">
      <h3 className="eyebrow">{title}</h3>
      {children}
    </section>
  );
}

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-neutral">{k}</dt>
          <dd className="num text-right text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Writes down a human decision. It sends nothing to the broker: there is no such path in this application. */
function ReviewForm({ d, initial, onDone }: { d: SignalDossier; initial: ReviewAction | null; onDone: () => void }) {
  const gate = useAdminGate();
  const user = useAuthStore((s) => s.user);
  const review = useReviewSignal(onDone);
  const active = d.status === 'new' || d.status === 'under_review';
  const reopenable = d.status === 'rejected' && d.rejectedBy === 'ai_reviewer';
  const actions: ReviewAction[] = active ? ['approve', 'reject'] : reopenable ? ['reopen'] : [];
  const [action, setAction] = useState<ReviewAction | null>(initial);
  const [reviewer, setReviewer] = useState(user?.name ?? '');
  const [note, setNote] = useState('');

  useEffect(() => setAction(initial), [initial, d.id]);

  if (actions.length === 0) return null;
  const chosen = action && actions.includes(action) ? action : null;
  const ready = !!chosen && reviewer.trim().length > 0 && note.trim().length > 0;
  return (
    <Section title="Your decision">
      <div className="flex gap-2 rounded-card border border-warn/30 bg-warn-faint px-3 py-2 text-xs text-ink-muted">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <p>Approving records your decision. AURUM AI does not place trades.</p>
      </div>
      <div className="flex gap-1.5">
        {actions.map((a) => (
          <Button key={a} size="sm" variant={chosen === a ? 'primary' : 'secondary'} aria-pressed={chosen === a} onClick={() => setAction(a)}>
            {ACTION_LABEL[a]}
          </Button>
        ))}
      </div>
      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        Reviewer
        <input className="field" value={reviewer} maxLength={60} onChange={(e) => setReviewer(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-muted">
        Note
        <textarea className="field min-h-20 py-2" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="flex justify-end">
        <Button
          size="sm"
          variant="primary"
          loading={review.isPending}
          disabled={!gate.allowed || !ready}
          disabledReason={gate.reason ?? 'Choose an action and add your name and a note'}
          onClick={() => chosen && review.mutate({ action: chosen, id: d.id, body: { reviewer: reviewer.trim(), note: note.trim() } })}
        >
          Record decision
        </Button>
      </div>
    </Section>
  );
}

function Dossier({ d, digits, action, onDone }: { d: SignalDossier; digits: number; action: ReviewAction | null; onDone: () => void }) {
  const st = SIGNAL_STATUS[d.status];
  const ai = d.aiReview;
  const missing = Object.entries(d.availability ?? {}).filter(([, v]) => v !== 'available');
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5">
        <Badge tone={st?.tone ?? 'neutral'}>{st?.label ?? d.status}</Badge>
        <Badge tone="gold">Grade {d.grade}</Badge>
        <Badge tone="info">{humanize(d.reviewStatus)}</Badge>
      </div>
      <SignalChart signal={d} digits={digits} />
      <Rows
        rows={[
          ['Entry zone', `${formatNumber(d.entryZone[0], digits)} – ${formatNumber(d.entryZone[1], digits)}`],
          ['Stop loss', formatNumber(d.stopLoss, digits)],
          ['Take profit', formatNumber(d.takeProfit, digits)],
          ['Second target', d.takeProfit2 === null ? 'None' : formatNumber(d.takeProfit2, digits)],
          ['Risk / reward', `1 : ${d.riskReward.toFixed(2)}`],
          ['Confidence score', `${d.confidence}/100`],
          ['Estimated size', d.suggestedVolume === null ? (d.volumeReason ?? 'Unavailable') : `${d.suggestedVolume} lots (estimate)`],
          ['Expires', `${formatDateTime(d.expiresAt)} UTC`],
        ]}
      />
      <p className="text-xs text-neutral">{d.disclaimer}</p>
      <Section title="Invalidation">
        <p className="text-xs text-ink-muted">{d.invalidation}</p>
      </Section>
      <Section title="Evidence">
        <ul className="flex flex-col gap-1.5 text-xs text-ink-muted">
          {d.evidenceDetail.map((e) => (
            <li key={e.component} className="flex justify-between gap-3">
              <span>{e.sentence}</span>
              <span className="num shrink-0 text-ink">
                {e.points}/{e.maxPoints}
              </span>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Gates">
        <ul className="flex flex-col gap-1.5 text-xs text-ink-muted">
          {d.gates.map((g) => (
            <li key={g.gate} className="flex items-center justify-between gap-3">
              <span>{humanize(g.gate)}</span>
              <Badge tone={GATE[g.result].tone}>{GATE[g.result].label}</Badge>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="AI review">
        {ai.status === 'reviewed' && ai.review ? (
          <div className="flex flex-col gap-2 text-xs text-ink-muted">
            <div className="flex items-center gap-2">
              <Badge tone={VERDICT[ai.verdict ?? ''] ?? 'neutral'}>{humanize(ai.verdict ?? 'unknown')}</Badge>
              {!!ai.adjustment && <span className="num">{ai.adjustment} confidence points</span>}
            </div>
            <p>{ai.review.summary}</p>
            <p>
              <span className="text-neutral">Bullish case: </span>
              {ai.review.bullishCase}
            </p>
            <p>
              <span className="text-neutral">Bearish case: </span>
              {ai.review.bearishCase}
            </p>
            {ai.review.risks.length > 0 && (
              <ul className="list-inside list-disc space-y-0.5">
                {ai.review.risks.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <p className="text-xs text-ink-muted">AI review unavailable ({humanize(ai.reason ?? 'unknown')}). The candidate stands on its rule-based evidence and its confidence is capped.</p>
        )}
      </Section>
      {missing.length > 0 && (
        <Section title="Missing data">
          <ul className="flex flex-col gap-1 text-xs text-ink-muted">
            {missing.map(([k, v]) => (
              <li key={k} className="flex justify-between gap-3">
                <span>{humanize(k)}</span>
                <span>{humanize(v)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Status history">
        <ul className="flex flex-col gap-1.5 text-xs text-ink-muted">
          {d.statusHistory.map((h) => (
            <li key={`${h.at}-${h.to}`}>
              <span className="text-ink">{SIGNAL_STATUS[h.to as keyof typeof SIGNAL_STATUS]?.label ?? h.to}</span> by {humanize(h.actor)}
              <span className="num text-neutral"> · {formatDateTime(h.at)} UTC</span>
              {h.note && <p className="mt-0.5">{h.note}</p>}
            </li>
          ))}
        </ul>
      </Section>
      {d.outcome && (
        <Section title="Hypothetical outcome">
          <Rows
            rows={[
              ['Result', humanize(d.outcome.result)],
              ['Entry touched', d.outcome.touched ? 'Yes' : 'No'],
              ['R multiple', d.outcome.rMultiple === null ? 'Not resolved' : d.outcome.rMultiple.toFixed(2)],
            ]}
          />
        </Section>
      )}
      <ReviewForm d={d} initial={action} onDone={onDone} />
    </div>
  );
}

interface Props {
  id: string | null;
  digits?: number;
  /** The action the operator clicked on the card, preselected in the form. */
  action?: ReviewAction | null;
  onClose: () => void;
}

/** A candidate's full dossier, and the only place a human decision is recorded. */
export function SignalDrawer({ id, digits = 5, action = null, onClose }: Props) {
  const q = useDossier(id);
  const d = q.data?.data;
  return (
    <Drawer open={!!id} title={d ? `${d.instrument} ${d.direction}` : 'Signal'} subtitle={d ? `${humanize(d.strategy)} · ${d.timeframe}` : undefined} onClose={onClose} width="w-[min(96vw,960px)]">
      <QueryState query={q} isEmpty={(r) => !r.data}>
        {(r) => r.data && <Dossier d={r.data} digits={digits} action={action} onDone={onClose} />}
      </QueryState>
    </Drawer>
  );
}
