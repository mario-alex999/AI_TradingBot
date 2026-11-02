import type { AssetClass, Instrument, Timeframe } from '@/types/market';

/**
 * Seed list used until GET /market/instruments responds. The live list from
 * the backend always takes precedence.
 */
export const DEFAULT_INSTRUMENTS: Instrument[] = [
  { symbol: 'EURUSD', name: 'Euro / US Dollar', assetClass: 'forex', enabled: true },
  { symbol: 'GBPUSD', name: 'British Pound / US Dollar', assetClass: 'forex', enabled: true },
  { symbol: 'XAUUSD', name: 'Gold / US Dollar', assetClass: 'commodities', enabled: true },
  { symbol: 'US30', name: 'Dow Jones Industrial Average', assetClass: 'indices', enabled: true },
];

export const TIMEFRAMES: Timeframe[] = ['D1', 'H4', 'H1', 'M15', 'M5'];

export const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  D1: 86_400,
  H4: 14_400,
  H1: 3_600,
  M15: 900,
  M5: 300,
};

export const ASSET_CLASS_LABEL: Record<AssetClass, string> = {
  forex: 'Forex',
  commodities: 'Commodities',
  indices: 'Indices',
  crypto: 'Cryptocurrency',
};
