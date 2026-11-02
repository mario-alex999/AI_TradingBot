export type AssetClass = 'forex' | 'commodities' | 'indices' | 'crypto';
export type Timeframe = 'D1' | 'H4' | 'H1' | 'M15' | 'M5';
export type MarketStatus = 'open' | 'closed' | 'halted' | 'unknown';

export interface Instrument {
  symbol: string; // canonical name, e.g. EURUSD
  name: string;
  assetClass: AssetClass;
  enabled: boolean;
}

export interface Quote {
  symbol: string;
  bid: number;
  ask: number;
  spread: number; // in points
  dailyChangePct: number;
  dailyHigh: number;
  dailyLow: number;
  volume: number | null;
  status: MarketStatus;
  digits: number;
  updatedAt: string; // ISO-8601 UTC
}

export interface Candle {
  time: number; // UNIX seconds, UTC
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface InstrumentSpecification {
  symbol: string;
  brokerSymbol: string | null;
  assetClass: AssetClass;
  enabled: boolean;
  digits: number | null;
  point: number | null;
  contractSize: number | null;
  tickSize: number | null;
  tickValue: number | null;
}

/** Overlays are produced by the analysis backend — the frontend never derives them. */
export type OverlayKind =
  | 'support_resistance'
  | 'order_block'
  | 'fair_value_gap'
  | 'liquidity_zone'
  | 'previous_day_hl'
  | 'previous_week_hl'
  | 'asian_session_hl'
  | 'london_session_hl'
  | 'break_of_structure'
  | 'change_of_character'
  | 'equal_highs_lows';

export interface ChartOverlay {
  id: string;
  kind: OverlayKind;
  label: string;
  /** A horizontal level, or a zone when priceTo is present. */
  price: number;
  priceTo?: number;
  from?: number; // UNIX seconds
  to?: number;
  direction?: 'bullish' | 'bearish';
}
