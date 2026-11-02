import type { OverlayKind } from '@/types/market';

export const OVERLAY_LABEL: Record<OverlayKind, string> = {
  support_resistance: 'Support & resistance',
  order_block: 'Order blocks',
  fair_value_gap: 'Fair value gaps',
  liquidity_zone: 'Liquidity zones',
  previous_day_hl: 'Previous day high / low',
  previous_week_hl: 'Previous week high / low',
  asian_session_hl: 'Asian session high / low',
  london_session_hl: 'London session high / low',
  break_of_structure: 'Break of structure',
  change_of_character: 'Change of character',
  equal_highs_lows: 'Equal highs / lows',
};
