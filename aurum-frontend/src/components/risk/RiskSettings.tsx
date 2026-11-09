import { useEffect, useMemo, useState } from 'react';
import { Settings2 } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { EmptyState } from '@/components/common/EmptyState';
import { Select } from '@/components/common/Select';
import { useRiskAdminGate, useRiskConfig, useUpdateRiskConfig } from '@/hooks/useRisk';
import { ApiError } from '@/services/api';
import { toast } from '@/stores/toastStore';
import type { ConfigUpdate, RiskLimits, RiskProfile } from '@/types/risk';
import { describeError } from '@/utils/errors';
import { formatDateTime } from '@/utils/format';

const FIELDS: { key: keyof RiskLimits; label: string; unit: '%' | 'count' | 'ratio'; group: string }[] = [
  { key: 'riskPerTradePct', label: 'Risk per trade', unit: '%', group: 'Per trade' },
  { key: 'maxRiskPerTradePct', label: 'Maximum risk per trade', unit: '%', group: 'Per trade' },
  { key: 'minRiskReward', label: 'Minimum reward-to-risk', unit: 'ratio', group: 'Per trade' },
  { key: 'maxDailyLossPct', label: 'Daily loss limit', unit: '%', group: 'Account' },
  { key: 'maxWeeklyLossPct', label: 'Weekly loss limit', unit: '%', group: 'Account' },
  { key: 'maxMonthlyLossPct', label: 'Monthly loss limit', unit: '%', group: 'Account' },
  { key: 'maxDrawdownPct', label: 'Maximum drawdown', unit: '%', group: 'Account' },
  { key: 'maxConsecutiveLosses', label: 'Consecutive losses', unit: 'count', group: 'Account' },
  { key: 'maxTradesPerDay', label: 'Trades per day', unit: 'count', group: 'Account' },
  { key: 'maxMarginUtilizationPct', label: 'Margin utilisation', unit: '%', group: 'Account' },
  { key: 'maxOpenPositions', label: 'Open positions', unit: 'count', group: 'Portfolio' },
  { key: 'maxTotalOpenRiskPct', label: 'Total open risk', unit: '%', group: 'Portfolio' },
  { key: 'maxSymbolRiskPct', label: 'Risk per instrument', unit: '%', group: 'Portfolio' },
  { key: 'maxAssetClassRiskPct', label: 'Risk per asset class', unit: '%', group: 'Portfolio' },
  { key: 'maxCorrelatedRiskPct', label: 'Correlated risk', unit: '%', group: 'Portfolio' },
];
const GROUPS = ['Per trade', 'Account', 'Portfolio'];
const PROFILES: RiskProfile[] = ['conservative', 'moderate', 'aggressive'];

type Draft = Record<keyof RiskLimits, string>;

/** Section E: versioned risk settings. Every change needs a reason; loosening a limit needs a second confirmation. */
export function RiskSettings() {
  const q = useRiskConfig();
  const gate = useRiskAdminGate();
  const update = useUpdateRiskConfig();
  const view = q.data?.data;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [profile, setProfile] = useState<RiskProfile | ''>('');
  const [reason, setReason] = useState('');
  const [weakening, setWeakening] = useState<{ body: ConfigUpdate; message: string } | null>(null);

  const original = useMemo(() => {
    if (!view) return null;
    return Object.fromEntries(FIELDS.map((f) => [f.key, String(view.config[f.key])])) as Draft;
  }, [view]);
  useEffect(() => setDraft(original), [original]);

  if (q.isError) {
    const e = describeError(q.error);
    return <EmptyState title={e.title} description={e.description} />;
  }
  if (!view || !draft || !original) return <EmptyState title="Risk settings not available" description="Settings live on the backend and are versioned there." icon={<Settings2 className="h-5 w-5" />} />;

  const changes = Object.fromEntries(
    FIELDS.filter((f) => draft[f.key] !== original[f.key] && draft[f.key] !== '').map((f) => [f.key, Number(draft[f.key])]),
  ) as Partial<RiskLimits>;
  const invalid = FIELDS.some((f) => draft[f.key] === '' || !Number.isFinite(Number(draft[f.key])) || Number(draft[f.key]) <= 0);
  const profileChange = profile && profile !== view.config.profile ? profile : undefined;
  const dirty = Object.keys(changes).length > 0 || !!profileChange;
  const canSave = gate.allowed && dirty && !invalid && reason.trim().length >= 5;

  const send = (body: ConfigUpdate) =>
    update.mutate(body, {
      onSuccess: () => {
        setReason('');
        setProfile('');
        setWeakening(null);
      },
      onError: (e) => {
        if (e instanceof ApiError && e.code === 'WEAKENING_NOT_ACKNOWLEDGED') setWeakening({ body, message: e.message });
        else {
          toast({ tone: 'error', title: 'Settings not saved', description: describeError(e).description });
          if (e instanceof ApiError && e.code === 'VERSION_CONFLICT') void q.refetch();
        }
      },
    });

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          icon={<Settings2 className="h-4 w-4" />}
          title="Risk settings"
          subtitle="Saved as a new version on the backend. Earlier versions are kept; active locks are never released by a settings change."
          actions={
            <Badge tone="neutral">
              v{view.version} · {view.config.profile}
            </Badge>
          }
        />
        <CardBody className="flex flex-col gap-5">
          <div className="grid gap-3 md:grid-cols-[240px_minmax(0,1fr)]">
            <Select
              label="Apply a profile"
              value={profile}
              options={[{ value: '', label: 'Keep current values' }, ...PROFILES.map((p) => ({ value: p, label: p.charAt(0).toUpperCase() + p.slice(1) }))]}
              onChange={(v) => setProfile(v as RiskProfile | '')}
              disabled={!gate.allowed}
            />
            <p className="self-end text-2xs text-neutral">A profile replaces every limit below with its preset. Edit individual limits instead to change only those.</p>
          </div>
          {GROUPS.map((g) => (
            <fieldset key={g} disabled={!gate.allowed || !!profileChange} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <legend className="eyebrow mb-2">{g}</legend>
              {FIELDS.filter((f) => f.group === g).map((f) => {
                const changed = draft[f.key] !== original[f.key];
                return (
                  <label key={f.key} className="flex flex-col gap-1 text-xs text-ink-muted">
                    <span>
                      {f.label} {f.unit === '%' ? '(%)' : f.unit === 'ratio' ? '(R)' : ''}
                      {changed && <span className="ml-1 text-gold">· was {original[f.key]}</span>}
                    </span>
                    <input
                      type="number"
                      step={f.unit === 'count' ? 1 : 0.05}
                      min={0}
                      className="field num"
                      value={draft[f.key]}
                      onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                    />
                  </label>
                );
              })}
            </fieldset>
          ))}
          <div className="grid gap-3 border-t border-line-soft pt-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Reason for the change (audit log)
              <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} disabled={!gate.allowed} placeholder="At least 5 characters" />
            </label>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                disabled={!dirty}
                onClick={() => {
                  setDraft(original);
                  setProfile('');
                }}
              >
                Discard
              </Button>
              <Button
                disabled={!canSave}
                disabledReason={gate.reason ?? (invalid ? 'Every limit must be a positive number' : !dirty ? 'Nothing changed' : 'Give a reason of at least 5 characters')}
                loading={update.isPending}
                onClick={() => send({ baseVersion: view.version, reason: reason.trim(), ...(profileChange ? { profile: profileChange } : { changes }) })}
              >
                Save new version
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Version history" subtitle="Append-only. Loosened limits are listed for each version." />
        <CardBody>
          <ol className="flex flex-col gap-2">
            {view.history.map((v) => (
              <li key={v.version} className="flex flex-wrap items-baseline gap-2 border-b border-line-soft pb-2 text-xs last:border-0">
                <Badge tone={v.version === view.version ? 'gold' : 'neutral'}>v{v.version}</Badge>
                <span className="text-ink">{v.reason}</span>
                <span className="text-neutral">
                  {v.profile} · {v.createdBy} · {formatDateTime(v.createdAt)}
                </span>
                {v.weakened.length > 0 && <span className="w-full text-warn">Loosened: {v.weakened.join('; ')}</span>}
              </li>
            ))}
          </ol>
        </CardBody>
      </Card>

      <ConfirmDialog
        open={!!weakening}
        tone="danger"
        title="This change loosens risk limits"
        confirmLabel="Loosen limits"
        description={
          <>
            <span className="block">{weakening?.message}</span>
            <span className="mt-2 block">The change is recorded with your reason. Existing locks stay active.</span>
          </>
        }
        onCancel={() => setWeakening(null)}
        onConfirm={() => weakening && send({ ...weakening.body, acknowledgeWeakening: true })}
      />
    </div>
  );
}
