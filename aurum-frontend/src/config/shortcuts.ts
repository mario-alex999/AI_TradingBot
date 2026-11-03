/** Two-key "go to" shortcuts: press G, then the letter. */
export const GO_TO: { key: string; path: string; label: string }[] = [
  { key: 'd', path: '/', label: 'Dashboard' },
  { key: 'm', path: '/market', label: 'Market Overview' },
  { key: 'c', path: '/charts', label: 'Charts & Analysis' },
  { key: 's', path: '/structure', label: 'Market Structure' },
  { key: 'i', path: '/institutional', label: 'Institutional Intelligence' },
  { key: 'o', path: '/cot', label: 'COT Analysis' },
  { key: 't', path: '/sessions', label: 'Trading Sessions' },
  { key: 'a', path: '/signals', label: 'AI Signals' },
  { key: 'r', path: '/risk', label: 'Risk Management' },
  { key: 'p', path: '/performance', label: 'Performance Analytics' },
  { key: 'e', path: '/data', label: 'Data Centre' },
  { key: 'l', path: '/agents', label: 'Agent Centre' },
  { key: 'n', path: '/notifications', label: 'Notifications' },
  { key: ',', path: '/settings', label: 'Settings' },
];

export const GENERAL_SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ['Ctrl', 'K'], label: 'Search pages and instruments' },
  { keys: ['/'], label: 'Search pages and instruments' },
  { keys: ['['], label: 'Collapse or expand the sidebar' },
  { keys: ['?'], label: 'Show keyboard shortcuts' },
  { keys: ['Esc'], label: 'Close a panel, menu or dialog' },
];
