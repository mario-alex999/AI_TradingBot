import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Check, Minus } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody } from '@/components/common/Card';
import { Tabs } from '@/components/common/Tabs';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { SettingRow } from '@/components/common/SettingRow';
import { Select } from '@/components/common/Select';
import { Switch } from '@/components/common/Switch';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { SessionConfigForm } from '@/components/settings/SessionConfigForm';
import { useSettingsStore, type DateFormat, type NumberLocale } from '@/stores/settingsStore';
import { useAuthStore } from '@/stores/authStore';
import { useSignOut } from '@/hooks/useSession';
import { toast } from '@/stores/toastStore';
import { DISPLAY_TIMEZONES } from '@/config/sessions';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';
import { CATEGORY_LABEL } from '@/config/notifications';
import { ROLE_LABEL, ROLE_PERMISSIONS } from '@/config/permissions';
import { env } from '@/config/env';
import { formatDateTime, formatNumber } from '@/utils/format';
import { useUiStore } from '@/stores/uiStore';
import type { NotificationCategory } from '@/types/system';
import type { Permission, Role } from '@/types/auth';
import type { Timeframe } from '@/types/market';

type Tab = 'general' | 'market' | 'data' | 'notifications' | 'security' | 'about';
const TABS: { id: Tab; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'market', label: 'Market' },
  { id: 'data', label: 'Data' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'security', label: 'Security' },
  { id: 'about', label: 'About' },
];

const BACKEND_MANAGED = <span className="text-xs text-neutral">Managed by the backend · read-only here</span>;

function General() {
  const s = useSettingsStore();
  return (
    <>
      <SettingRow title="Application theme" description="Light, dark, or follow your operating system's setting. Also switchable from the moon/sun button in the top bar.">
        <SegmentedControl
          label="Application theme"
          value={s.theme}
          options={[{ value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }, { value: 'system', label: 'System' }]}
          onChange={(theme) => s.update({ theme })}
        />
      </SettingRow>
      <SettingRow title="Display timezone" description="Used for session times and timestamps shown in your local context. Market data timestamps are stored in UTC.">
        <Select label="Display timezone" hideLabel value={s.displayTimezone} onChange={s.setDisplayTimezone} options={DISPLAY_TIMEZONES.map((z) => ({ value: z, label: z }))} />
      </SettingRow>
      <SettingRow title="Date format">
        <Select
          label="Date format"
          hideLabel
          value={s.dateFormat}
          onChange={(v) => s.update({ dateFormat: v as DateFormat })}
          options={[
            { value: 'dd MMM yyyy', label: '01 Nov 2026' },
            { value: 'yyyy-MM-dd', label: '2026-11-01' },
            { value: 'MM/dd/yyyy', label: '11/01/2026' },
          ]}
        />
      </SettingRow>
      <SettingRow title="Number format" description={<span className="num">Preview: {formatNumber(1234567.891, 2, s.numberLocale)}</span>}>
        <Select
          label="Number format"
          hideLabel
          value={s.numberLocale}
          onChange={(v) => s.update({ numberLocale: v as NumberLocale })}
          options={[
            { value: 'en-US', label: '1,234,567.89' },
            { value: 'en-GB', label: '1,234,567.89 (UK)' },
            { value: 'de-DE', label: '1.234.567,89' },
            { value: 'fr-FR', label: '1 234 567,89' },
          ]}
        />
      </SettingRow>
    </>
  );
}

function Market() {
  const s = useSettingsStore();
  const navigate = useNavigate();
  return (
    <>
      <SettingRow title="Instruments" description="The instrument universe is configured on the backend.">
        <div className="flex flex-wrap items-center gap-2">
          {DEFAULT_INSTRUMENTS.map((i) => (
            <Badge key={i.symbol}>{i.symbol}</Badge>
          ))}
          <Button size="sm" variant="ghost" onClick={() => navigate('/data')}>
            Manage in Data Centre
          </Button>
        </div>
      </SettingRow>
      <SettingRow title="Default chart" description="Instrument and timeframe opened by instrument detail pages.">
        <div className="grid grid-cols-2 gap-2">
          <Select label="Default instrument" hideLabel value={s.defaultInstrument} onChange={(v) => s.update({ defaultInstrument: v })} options={DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))} />
          <Select label="Default timeframe" hideLabel value={s.defaultTimeframe} onChange={(v) => s.update({ defaultTimeframe: v as Timeframe })} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
        </div>
      </SettingRow>
      <SettingRow title="Timeframes" description="Collected by Module 01.">
        <div className="flex flex-wrap gap-1.5">
          {TIMEFRAMES.map((t) => (
            <Badge key={t}>{t}</Badge>
          ))}
        </div>
      </SettingRow>
      <SettingRow title="Price display">
        <Switch label="Show spreads in pips" description="Otherwise spreads are shown in broker points." checked={s.showSpreadInPips} onChange={(v) => s.update({ showSpreadInPips: v })} />
      </SettingRow>
      <SettingRow title="Trading sessions" description="Each session is defined in its home market's timezone so daylight saving is applied automatically.">
        <SessionConfigForm />
      </SettingRow>
    </>
  );
}

function Data() {
  const { mockMode, setMockMode, demoTicks, update } = useSettingsStore();
  const [confirm, setConfirm] = useState(false);
  return (
    <>
      <SettingRow
        title="Demo data mode"
        description="When on, market, COT and Data Centre previews use the clearly labelled DEMO DATA layer. Account, signals and performance are never simulated."
      >
        <Switch label="Use demo data" checked={mockMode} onChange={() => setConfirm(true)} />
        <ConfirmDialog
          open={confirm}
          title={mockMode ? 'Switch to backend data?' : 'Switch to demo data?'}
          description={
            mockMode ? (
              <>
                All panels will request data from <span className="num">{env.apiBaseUrl}</span>. Panels show connection errors if the backend is offline.
              </>
            ) : (
              'Panels will display synthetic DEMO DATA, labelled on every panel. Operational actions are disabled in this mode.'
            )
          }
          confirmLabel={mockMode ? 'Use backend data' : 'Use demo data'}
          onCancel={() => setConfirm(false)}
          onConfirm={() => {
            setMockMode(!mockMode);
            setConfirm(false);
            toast({ tone: 'info', title: mockMode ? 'Backend data mode' : 'Demo data mode' });
          }}
        />
      </SettingRow>
      <SettingRow title="Live demo prices" description="In demo mode only: demo quotes move every two seconds so the interface can be tried out. Never applied to backend data.">
        <Switch label="Animate demo quotes" checked={demoTicks} disabled={!mockMode} disabledReason="Only available in demo-data mode" onChange={(v) => update({ demoTicks: v })} />
      </SettingRow>
      <SettingRow title="Backend API" description="Set VITE_API_BASE_URL in .env.local and restart the dev server to change it.">
        <code className="num rounded bg-base-raised px-2 py-1 text-xs text-ink-muted">{env.apiBaseUrl}</code>
      </SettingRow>
      <SettingRow title="Collection frequency">{BACKEND_MANAGED}</SettingRow>
      <SettingRow title="Historical candle count">{BACKEND_MANAGED}</SettingRow>
      <SettingRow title="Data retention">{BACKEND_MANAGED}</SettingRow>
      <SettingRow title="Export preferences" description="CSV and Parquet exports are generated server-side from GET /market/export.">
        {BACKEND_MANAGED}
      </SettingRow>
    </>
  );
}

function Notifications() {
  const { mutedCategories, update } = useSettingsStore();
  const toggle = (c: NotificationCategory) =>
    update({ mutedCategories: mutedCategories.includes(c) ? mutedCategories.filter((x) => x !== c) : [...mutedCategories, c] });
  return (
    <SettingRow title="Alert categories" description="Choose which alerts raise in-app toasts. All alerts remain visible in the Notifications Centre.">
      <div className="flex flex-col gap-3">
        {(Object.keys(CATEGORY_LABEL) as NotificationCategory[]).map((c) => (
          <Switch key={c} label={CATEGORY_LABEL[c]} checked={!mutedCategories.includes(c)} onChange={() => toggle(c)} />
        ))}
      </div>
    </SettingRow>
  );
}

const PERMISSION_LABEL: Record<Permission, string> = {
  'view:dashboard': 'View dashboards & markets',
  'view:trading': 'View signals, trades & performance',
  'view:risk': 'View risk management',
  'view:system': 'View Agent Centre',
  'data:collect': 'Trigger data collection',
  'data:export': 'Export market data',
  'config:instruments': 'Edit instrument configuration',
  'config:agents': 'Configure agents',
  'signals:review': 'Review AI signals',
  'risk:manage': 'Change risk settings and locks',
  'settings:security': 'Manage security settings',
};

function Security() {
  const user = useAuthStore((s) => s.user);
  const signOut = useSignOut();
  const server = user?.mode === 'server';
  const navigate = useNavigate();
  const roles = Object.keys(ROLE_LABEL) as Role[];
  const { key: adminKey, setKey: setAdminKey, clear: clearAdminKey } = useAdminKeyStore();
  return (
    <>
      <SettingRow title="User profile">
        <dl className="grid gap-1 text-sm">
          <div className="flex gap-2"><dt className="w-16 text-neutral">Name</dt><dd className="text-ink">{user?.name}</dd></div>
          {server ? (
            <div className="flex gap-2"><dt className="w-16 text-neutral">User</dt><dd className="num text-ink">{user?.username}</dd></div>
          ) : (
            <div className="flex gap-2"><dt className="w-16 text-neutral">Email</dt><dd className="text-ink">{user?.email}</dd></div>
          )}
          <div className="flex gap-2"><dt className="w-16 text-neutral">Role</dt><dd>{user && <Badge tone="gold">{ROLE_LABEL[user.role]}</Badge>}</dd></div>
        </dl>
      </SettingRow>
      <SettingRow title="Password management" description="Passwords are handled exclusively by the backend authentication service and never touch browser storage.">
        <Button
          size="sm"
          disabled
          disabledReason={server ? 'An administrator resets passwords: python -m aurum.cli users reset-password <name>' : 'Demo workspace: sign in with a backend account first'}
        >
          Change password
        </Button>
      </SettingRow>
      <SettingRow title="Admin key" description="Needed to record signal decisions and start analysis runs. Held in memory only, so it is gone when you reload this page. It is never saved in the browser.">
        <div className="flex items-center gap-2">
          <input type="password" autoComplete="off" aria-label="Admin key" className="field" value={adminKey} onChange={(e) => setAdminKey(e.target.value)} />
          <Button size="sm" variant="ghost" disabled={!adminKey} disabledReason="No key entered" onClick={clearAdminKey}>
            Clear
          </Button>
        </div>
      </SettingRow>
      <SettingRow
        title="Session management"
        description={
          server
            ? 'Signed in with an httpOnly session cookie. It ends after inactivity or a fixed maximum time; signing out ends it on the server.'
            : 'This demo session lives in sessionStorage and ends when the tab closes.'
        }
      >
        <Button
          size="sm"
          variant="danger"
          onClick={() => {
            void signOut().then(() => navigate('/login'));
          }}
        >
          Sign out
        </Button>
      </SettingRow>
      <SettingRow title="Role permissions" description="UI visibility only. The backend authorises every request independently.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[460px] text-xs">
            <caption className="sr-only">Role permissions</caption>
            <thead>
              <tr className="border-b border-line text-neutral">
                <th scope="col" className="py-2 text-left font-medium">Permission</th>
                {roles.map((r) => (
                  <th key={r} scope="col" className="px-2 py-2 text-center font-medium">{ROLE_LABEL[r]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(Object.keys(PERMISSION_LABEL) as Permission[]).map((p) => (
                <tr key={p} className="border-b border-line-soft last:border-0">
                  <th scope="row" className="py-1.5 text-left font-normal text-ink-muted">{PERMISSION_LABEL[p]}</th>
                  {roles.map((r) => (
                    <td key={r} className="px-2 py-1.5 text-center">
                      {ROLE_PERMISSIONS[r].includes(p) ? <Check className="mx-auto h-3.5 w-3.5 text-up" aria-label="Allowed" /> : <Minus className="mx-auto h-3.5 w-3.5 text-line" aria-label="Not allowed" />}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SettingRow>
    </>
  );
}

const STACK = ['React 18', 'TypeScript (strict)', 'Vite', 'Tailwind CSS', 'TanStack Query', 'Zustand', 'Lightweight Charts', 'Recharts', 'React Hook Form + Zod', 'Python FastAPI backend'];

function About() {
  const setShortcutsOpen = useUiStore((s) => s.setShortcutsOpen);
  const rows: [string, string][] = [
    ['Built by', env.builtBy],
    ['Version', `v${__APP_VERSION__}`],
    ['Build', `${formatDateTime(__BUILD_TIME__)} UTC`],
    ['Commit', __COMMIT__],
    ['Backend API', env.apiBaseUrl],
  ];
  return (
    <>
      <SettingRow title="AURUM AI" description="Institutional market intelligence and AI-assisted trading command centre. Read-only: no order execution.">
        <dl className="grid gap-1.5 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="flex gap-3">
              <dt className="w-28 shrink-0 text-neutral">{k}</dt>
              <dd className={k === 'Commit' || k === 'Backend API' ? 'font-mono text-xs text-ink' : 'text-ink'} data-testid={k === 'Built by' ? 'about-built-by' : undefined}>
                {v}
              </dd>
            </div>
          ))}
        </dl>
      </SettingRow>
      <SettingRow title="Engineering" description="How the interface is put together.">
        <div className="flex flex-wrap gap-1.5">
          {STACK.map((s) => (
            <Badge key={s}>{s}</Badge>
          ))}
        </div>
      </SettingRow>
      <SettingRow title="Keyboard shortcuts" description="Press ? anywhere to see them.">
        <Button size="sm" onClick={() => setShortcutsOpen(true)}>
          Show shortcuts
        </Button>
      </SettingRow>
    </>
  );
}

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get('tab'))?.id ?? 'general') as Tab;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader eyebrow="System" title="Settings" description="Preferences are stored in this browser. Credentials are never stored client-side." />
      <Card>
        <div className="px-4 pt-2">
          <Tabs label="Settings sections" tabs={TABS} value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} />
        </div>
        <CardBody id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {tab === 'general' && <General />}
          {tab === 'market' && <Market />}
          {tab === 'data' && <Data />}
          {tab === 'notifications' && <Notifications />}
          {tab === 'security' && <Security />}
          {tab === 'about' && <About />}
        </CardBody>
      </Card>
    </div>
  );
}
