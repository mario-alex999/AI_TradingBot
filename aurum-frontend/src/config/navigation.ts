import {
  Activity,
  BarChart3,
  Bell,
  Bot,
  Building2,
  CandlestickChart,
  Clock3,
  Database,
  FlaskConical,
  Globe2,
  LayoutDashboard,
  type LucideIcon,
  PieChart,
  Settings,
  ShieldCheck,
  Sparkles,
  ArrowLeftRight,
} from 'lucide-react';
import type { Permission } from '@/types/auth';

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  permission: Permission;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      { path: '/', label: 'Dashboard', icon: LayoutDashboard, permission: 'view:dashboard' },
      { path: '/market', label: 'Market Overview', icon: Globe2, permission: 'view:dashboard' },
      { path: '/charts', label: 'Charts & Analysis', icon: CandlestickChart, permission: 'view:dashboard' },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { path: '/structure', label: 'Market Structure', icon: Activity, permission: 'view:dashboard' },
      { path: '/institutional', label: 'Institutional Intelligence', icon: Building2, permission: 'view:dashboard' },
      { path: '/cot', label: 'COT Analysis', icon: BarChart3, permission: 'view:dashboard' },
      { path: '/sessions', label: 'Trading Sessions', icon: Clock3, permission: 'view:dashboard' },
    ],
  },
  {
    label: 'Trading',
    items: [
      { path: '/signals', label: 'AI Signals', icon: Sparkles, permission: 'view:trading' },
      { path: '/trades', label: 'Trade Management', icon: ArrowLeftRight, permission: 'view:trading' },
      { path: '/risk', label: 'Risk Management', icon: ShieldCheck, permission: 'view:risk' },
      { path: '/backtesting', label: 'Backtesting', icon: FlaskConical, permission: 'view:trading' },
      { path: '/performance', label: 'Performance Analytics', icon: PieChart, permission: 'view:trading' },
    ],
  },
  {
    label: 'System',
    items: [
      { path: '/data', label: 'Data Centre', icon: Database, permission: 'view:dashboard' },
      { path: '/agents', label: 'Agent Centre', icon: Bot, permission: 'view:system' },
      { path: '/notifications', label: 'Notifications', icon: Bell, permission: 'view:dashboard' },
      { path: '/settings', label: 'Settings', icon: Settings, permission: 'view:dashboard' },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

export function titleForPath(pathname: string): string {
  if (pathname.startsWith('/market/')) return 'Instrument Analysis';
  const match = ALL_NAV_ITEMS.filter((i) => (i.path === '/' ? pathname === '/' : pathname.startsWith(i.path))).sort(
    (a, b) => b.path.length - a.path.length,
  )[0];
  return match?.label ?? 'AURUM AI';
}
