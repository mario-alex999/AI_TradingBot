import type { Timeframe } from './market';

export type Bias = 'bullish' | 'bearish' | 'ranging' | 'awaiting';

export interface StructureEvent {
  type: 'HH' | 'HL' | 'LH' | 'LL' | 'BOS' | 'CHOCH' | 'displacement' | 'liquidity_sweep' | 'fvg' | 'order_block';
  price: number;
  time: string;
  direction?: 'bullish' | 'bearish';
}

export interface TimeframeStructure {
  timeframe: Timeframe;
  bias: Bias;
  events: StructureEvent[];
  premiumDiscount: { equilibrium: number; rangeHigh: number; rangeLow: number } | null;
}

export interface MarketStructureReport {
  instrument: string;
  generatedAt: string;
  timeframes: TimeframeStructure[];
}

export interface CotPositionRow {
  reportDate: string; // Tuesday "as of" date
  publicationDate: string; // usually the following Friday
  openInterest: number;
  commercialLong: number;
  commercialShort: number;
  nonCommercialLong: number;
  nonCommercialShort: number;
  nonReportableLong: number;
  nonReportableShort: number;
}

export interface CotReport {
  contract: string;
  instrument: string;
  reportType: string;
  source: string;
  updatedAt: string;
  history: CotPositionRow[];
}
