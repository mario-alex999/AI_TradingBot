import { useState } from 'react';
import { Lock, LockOpen, OctagonX } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { Select } from '@/components/common/Select';
import { useCreateLock, useReleaseLock, useRiskAdminGate, useRiskLocks } from '@/hooks/useRisk';
import type { RiskLock } from '@/types/risk';
import { describeError } from '@/utils/errors';
import { formatDateTime } from '@/utils/format';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';

const SCOPES: { value: RiskLock['scope']; label: string }[] = [
  { value: 'global', label: 'Everything (kill switch)' },
  { value: 'instrument', label: 'One instrument' },
  { value: 'strategy', label: 'One strategy' },
  { value: 'account', label: 'This account' },
];

function scopeText(l: RiskLock): string {
  return l.scope === 'global' ? 'All trading' : `${l.scope}${l.scopeValue ? `: ${l.scopeValue}` : ''}`;
}

function ReleaseForm({ lock, onDone }: { lock: RiskLock; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const release = useReleaseLock(onDone);
  const ok = reason.trim().length >= 5;
  return (
    <form
      className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) release.mutate({ id: lock.lockId, reason: reason.trim() });
      }}
    >
      <label className="flex flex-1 flex-col gap-1 text-xs text-ink-muted">
        Release reason (recorded in the audit log)
        <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is it safe to resume?" />
      </label>
      <Button type="submit" variant="danger" size="sm" disabled={!ok} loading={release.isPending}>
        Release lock
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={onDone}>
        Cancel
      </Button>
    </form>
  );
}

/** Section B: active locks, the manual kill switch and lock history. */
export function LocksPanel() {
  const q = useRiskLocks();
  const gate = useRiskAdminGate();
  const [releasing, setReleasing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [scope, setScope] = useState<RiskLock['scope']>('global');
  const [value, setValue] = useState('');
  const [reason, setReason] = useState('');
  const create = useCreateLock(() => {
    setCreating(false);
    setReason('');
    setValue('');
  });
  const data = q.data?.data;
  const active = data?.active ?? [];
  const recent = (data?.history ?? []).filter((l) => l.status !== 'active').slice(0, 5);
  const needsValue = scope === 'instrument' || scope === 'strategy';
  const canCreate = reason.trim().length >= 5 && (!needsValue || value.trim().length > 0);

  return (
    <Card>
      <CardHeader
        icon={<Lock className="h-4 w-4" />}
        title="Trading locks"
        subtitle="Locks survive restarts and config changes. Loss and drawdown locks need a written reason to release."
        actions={
          <Button
            size="sm"
            variant="danger"
            icon={<OctagonX className="h-3.5 w-3.5" />}
            disabled={!gate.allowed}
            disabledReason={gate.reason}
            onClick={() => setCreating((v) => !v)}
          >
            Activate lock
          </Button>
        }
      />
      <CardBody className="flex flex-col gap-4">
        {creating && (
          <form
            aria-label="Activate lock"
            className="grid gap-3 rounded-md border border-down/30 bg-down-faint/40 p-3 md:grid-cols-[200px_minmax(0,1fr)_minmax(0,2fr)_auto] md:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (canCreate) create.mutate({ scope, reason: reason.trim(), ...(needsValue ? { scopeValue: value.trim() } : {}) });
            }}
          >
            <Select label="Scope" value={scope} options={SCOPES} onChange={(v) => setScope(v as RiskLock['scope'])} />
            {scope === 'instrument' ? (
              <Select label="Instrument" value={value} options={[{ value: '', label: 'Choose…' }, ...DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))]} onChange={setValue} />
            ) : (
              <label className="flex flex-col gap-1 text-xs text-ink-muted">
                {scope === 'strategy' ? 'Strategy' : 'Value'}
                <input className="field" value={value} onChange={(e) => setValue(e.target.value)} disabled={!needsValue} placeholder={needsValue ? 'Strategy name' : 'Not needed'} />
              </label>
            )}
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Reason
              <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="At least 5 characters" />
            </label>
            <Button type="submit" variant="danger" size="sm" disabled={!canCreate} loading={create.isPending}>
              Lock
            </Button>
          </form>
        )}

        {q.isError ? (
          <p className="text-xs text-neutral">{describeError(q.error).description}</p>
        ) : active.length === 0 ? (
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <LockOpen className="h-4 w-4 text-up" /> {data ? 'No active locks.' : 'Lock status comes from the backend.'}
          </div>
        ) : (
          <ul className="flex flex-col gap-2" aria-label="Active locks">
            {active.map((l) => (
              <li key={l.lockId} className="rounded-md border border-down/30 bg-down-faint/40 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="down">{scopeText(l)}</Badge>
                  {l.ruleId && <Badge tone="neutral">{l.ruleId}</Badge>}
                  <span className="text-sm text-ink">{l.reason}</span>
                  <span className="ml-auto text-2xs text-neutral">
                    {l.triggerSource === 'manual' ? `By ${l.activatedBy}` : 'Automatic'} · {formatDateTime(l.activatedAt)}
                    {l.expiresAt ? ` · expires ${formatDateTime(l.expiresAt)}` : ' · manual release only'}
                  </span>
                  {releasing !== l.lockId && (
                    <Button size="sm" variant="secondary" disabled={!gate.allowed} disabledReason={gate.reason} onClick={() => setReleasing(l.lockId)}>
                      Release…
                    </Button>
                  )}
                </div>
                {releasing === l.lockId && <ReleaseForm lock={l} onDone={() => setReleasing(null)} />}
              </li>
            ))}
          </ul>
        )}

        {recent.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer text-ink-muted">Recent lock history ({recent.length})</summary>
            <ul className="mt-2 flex flex-col gap-1.5">
              {recent.map((l) => (
                <li key={l.lockId} className="flex flex-wrap gap-2 border-b border-line-soft pb-1.5 last:border-0">
                  <Badge tone="neutral">{l.status}</Badge>
                  <span className="text-ink">{scopeText(l)}</span>
                  <span className="text-neutral">{l.reason}</span>
                  <span className="ml-auto text-neutral">
                    {l.releasedBy ? `Released by ${l.releasedBy}: ${l.releaseReason}` : ''} {formatDateTime(l.releasedAt ?? l.activatedAt)}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </CardBody>
    </Card>
  );
}
