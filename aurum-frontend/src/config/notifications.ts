import type { NotificationCategory } from '@/types/system';

export const CATEGORY_LABEL: Record<NotificationCategory, string> = {
  mt5_disconnection: 'MT5 disconnection',
  database_failure: 'Database failure',
  collection_failure: 'Data collection failure',
  invalid_market_data: 'Invalid market data',
  market_movement: 'Important market movement',
  cot_update: 'COT update',
  signal_generated: 'AI signal generated',
  approval_required: 'Trade approval required',
  risk_limit: 'Risk limit warning',
};
