import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { BrainCircuit, ClipboardCheck, Newspaper, ShieldCheck, Workflow } from 'lucide-react';
import type { UseQueryResult } from '@tanstack/react-query';
import { useApprovalDashboard } from '@/hooks/useApprovals';
import { useRiskHealth, useRiskLocks } from '@/hooks/useRisk';
import { useNewsImpact } from '@/hooks/queries';
import { useDecisionStatus } from '@/hooks/useDecision';
import { useOrchOverview } from '@/hooks/useOrchestration';
import { useClock } from '@/hooks/useClock';
import { useAuthStore } from '@/stores/authStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { describeError } from '@/utils/errors';
import { formatDuration } from '@/utils/format';
import { humanize } from '@/utils/labels';
import { cn } from '@/utils/cn';
import type { Sourced } from '@/types/api';

type Tone = 'ok' | 'warn' | 'bad' | 'muted';
const DOT: Record<Tone, string> = { ok: 'bg-up', warn: 'bg-warn', bad: 'bg-down', muted: 'bg-line-control' };

interface TileProps {
  to: string;
  icon: ReactNode;
  title: string;
  value: ReactNode;
  detail: ReactNode;
  tone: Tone;
}

function Tile({ to, icon, title, value, detail, tone }: TileProps) {
  return (
    <Link
      to={to}
      className="group flex min-w-0 flex-col gap-1.5 rounded-card border border-line bg-base-card p-4 transition-colors hover:border-line-control hover:bg-base-elevated"
      data-testid={`cc-${to.slice(1)}`}
    >
      <span className="flex items-center justify-between gap-2 text-xs text-ink-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="text-neutral" aria-hidden>
            {icon}
          </span>
          {title}
        </span>
        <span className={cn('h-2 w-2 rounded-full', DOT[tone])} aria-label={`status ${tone}`} />
      </span>
      <span className="num truncate text-lg font-semibold text-ink">{value}</span>
      <span className="line-clamp-2 text-xs text-ink-muted">{detail}</span>
    </Link>
  );
}

/** When a module cannot answer (demo mode, not signed in, tables missing), say why instead of showing a number. */
function unavailable<T>(q: UseQueryResult<Sourced<T | null>>): string | null {
  if (q.isPending) return 'Loading…';
  if (q.isError) return describeError(q.error).title;
  if (!q.data?.data) return q.data?.source === 'demo' ? 'Needs the backend (demo mode)' : 'No data yet';
  return null;
}

/**
 * One row that ties the modules together: what is waiting for a person (Module 07), whether the risk engine can assess
 * (Module 06), the next high-impact release (Module 04), the decision engine (Module 05) and the workflows (Module 08).
 */
export function CommandCentre() {
  const now = useClock(30_000);
  const approvals = useApprovalDashboard();
  const riskHealth = useRiskHealth();
  const locks = useRiskLocks();
  const news = useNewsImpact({ importance: 'high', hoursAhead: 168 });
  const decision = useDecisionStatus();
  const orch = useOrchOverview();

  const server = useAuthStore((s) => s.user?.mode === 'server');
  const mockMode = useSettingsStore((s) => s.mockMode);
  // Modules 07 and 08 need a backend account; outside demo mode, say so rather than "no data".
  const signIn = (what: string) => (!mockMode && !server ? `Sign in with a backend account to see ${what}` : null);
  const ap = approvals.data?.data;
  const waiting = ap ? (ap.counts.PENDING ?? 0) + (ap.counts.UNDER_REVIEW ?? 0) : null;
  const rh = riskHealth.data?.data;
  const activeLocks = locks.data?.data?.active.length ?? 0;
  const next = news.data?.data?.nextEvent;
  const ds = decision.data?.data;
  const open = ds ? (ds.counts.new ?? 0) + (ds.counts.under_review ?? 0) : null;
  const ov = orch.data?.data;

  return (
    <section aria-label="Command centre" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <Tile
        to="/approvals"
        icon={<ClipboardCheck className="h-4 w-4" />}
        title="Trade approvals"
        value={waiting ?? '—'}
        detail={
          signIn('approvals') ??
          unavailable(approvals) ??
          (waiting ? `${waiting} idea${waiting === 1 ? '' : 's'} waiting for your decision` : `Queue clear · ${ap!.statistics.approved} approved, ${ap!.statistics.rejected} rejected`)
        }
        tone={waiting == null ? 'muted' : waiting > 0 ? 'warn' : 'ok'}
      />
      <Tile
        to="/risk"
        icon={<ShieldCheck className="h-4 w-4" />}
        title="Risk engine"
        value={rh ? (activeLocks ? `${activeLocks} lock${activeLocks === 1 ? '' : 's'}` : rh.status === 'ok' ? 'Ready' : humanize(rh.status)) : '—'}
        detail={
          unavailable(riskHealth) ??
          (activeLocks
            ? 'Trading locks are active: new ideas are blocked'
            : rh!.status === 'ok'
              ? 'Every idea is assessed before approval'
              : rh!.canAssess
                ? 'Missing inputs: assessments return review or error until fixed'
                : 'Cannot assess trade ideas: see Risk Management')
        }
        tone={!rh ? 'muted' : activeLocks || !rh.canAssess ? 'bad' : rh.status === 'ok' ? 'ok' : 'warn'}
      />
      <Tile
        to="/news"
        icon={<Newspaper className="h-4 w-4" />}
        title="Next high-impact news"
        value={next ? `${next.currency ?? ''} in ${formatDuration(Date.parse(next.scheduledAt) - now)}` : '—'}
        detail={unavailable(news) ?? (next ? next.name : 'No high-impact release in the next 7 days')}
        tone={!next ? 'muted' : Date.parse(next.scheduledAt) - now < 2 * 3_600_000 ? 'warn' : 'ok'}
      />
      <Tile
        to="/ai-engine"
        icon={<BrainCircuit className="h-4 w-4" />}
        title="AI decision engine"
        value={open == null ? '—' : `${open} candidate${open === 1 ? '' : 's'}`}
        detail={
          unavailable(decision) ??
          (ds!.recentRuns[0] ? `Last run ${ds!.recentRuns[0].symbol}: ${humanize(ds!.recentRuns[0].status)}` : 'No decision run yet') +
            ` · AI reviewer ${ds!.aiReviewer.enabled ? 'on' : 'off'}`
        }
        tone={!ds ? 'muted' : ds.schemaReady ? 'ok' : 'warn'}
      />
      <Tile
        to="/orchestration"
        icon={<Workflow className="h-4 w-4" />}
        title="Workflows"
        value={ov ? `${ov.workflows.active} active` : '—'}
        detail={
          signIn('workflows') ??
          unavailable(orch) ??
          (ov!.deadLettersOpen || ov!.executions.failed
            ? `${ov!.executions.failed} failed · ${ov!.deadLettersOpen} dead letter${ov!.deadLettersOpen === 1 ? '' : 's'}`
            : `${ov!.executions.running} running · no failures`)
        }
        tone={!ov ? 'muted' : ov.deadLettersOpen || ov.alerts.some((a) => a.level === 'critical') ? 'bad' : ov.executions.failed ? 'warn' : 'ok'}
      />
    </section>
  );
}
