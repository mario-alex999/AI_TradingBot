import { useClock } from '@/hooks/useClock';
import { useSessions } from '@/hooks/useSessions';
import { useHealth } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { formatDuration, formatInZone } from '@/utils/format';
import { describeError } from '@/utils/errors';

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className="num mt-0.5 text-sm text-ink">{value}</dd>
    </div>
  );
}

/**
 * Title row. A noun title, the brief's subtitle, then ONE finding computed from
 * live state: what needs attention right now (backend down, next session).
 */
export function WelcomeSection() {
  const now = useClock();
  const { activeLabel, sessions } = useSessions(now);
  const health = useHealth();
  const mock = useSettingsStore((s) => s.mockMode);
  const env = health.data?.environment === 'live' ? 'Live account' : 'Demo environment';

  const next = sessions
    .filter((s) => !s.isOpen && s.next)
    .sort((a, b) => a.countdownMs - b.countdownMs)[0];
  const sessionPart = next ? `${next.def.name.replace(' Session', '')} opens in ${formatDuration(next.countdownMs)}` : `${activeLabel} open`;
  const backendPart = health.isError
    ? `${describeError(health.error).title}${mock ? ', panels show demo data' : ''}`
    : health.data
      ? `Backend ${health.data.status === 'ok' ? 'healthy' : health.data.status}`
      : 'Checking backend';

  return (
    <section className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4" aria-labelledby="welcome-title">
      <div className="min-w-0">
        <h1 id="welcome-title" className="text-2xl font-semibold tracking-[-0.01em] text-ink">
          AURUM AI Market Intelligence
        </h1>
        <p className="mt-1 text-sm text-ink-muted">Your institutional market intelligence and AI-assisted trading command centre.</p>
        <p className="mt-2 text-sm text-ink" data-testid="finding">
          {backendPart} · {sessionPart}
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-4">
        <Meta label="Date" value={formatInZone(now, 'UTC', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })} />
        <Meta label="UTC time" value={formatInZone(now, 'UTC')} />
        <Meta label="Session" value={activeLabel} />
        <Meta label="Environment" value={env} />
      </dl>
    </section>
  );
}
