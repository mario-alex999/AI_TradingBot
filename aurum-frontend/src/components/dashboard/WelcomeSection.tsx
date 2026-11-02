import { useClock } from '@/hooks/useClock';
import { useSessions } from '@/hooks/useSessions';
import { useHealth } from '@/hooks/queries';
import { formatInZone } from '@/utils/format';

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[120px]">
      <dt className="eyebrow">{label}</dt>
      <dd className="num mt-1 text-sm text-ink">{value}</dd>
    </div>
  );
}

export function WelcomeSection() {
  const now = useClock();
  const { activeLabel } = useSessions(now);
  const health = useHealth();
  const env = health.data?.environment === 'live' ? 'Live account' : 'Demo environment';
  return (
    <section className="flex flex-wrap items-end justify-between gap-6 border-b border-line pb-5" aria-labelledby="welcome-title">
      <div>
        <p className="eyebrow text-gold">Command centre</p>
        <h1 id="welcome-title" className="mt-1 text-lg font-semibold tracking-[0.08em] text-ink sm:text-xl">
          AURUM AI MARKET INTELLIGENCE
        </h1>
        <p className="mt-1 text-sm text-ink-muted">Your institutional market intelligence and AI-assisted trading command centre.</p>
      </div>
      <dl className="flex flex-wrap gap-x-8 gap-y-3">
        <Meta label="Date" value={formatInZone(now, 'UTC', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })} />
        <Meta label="UTC time" value={formatInZone(now, 'UTC')} />
        <Meta label="Session" value={activeLabel} />
        <Meta label="Environment" value={env} />
      </dl>
    </section>
  );
}
