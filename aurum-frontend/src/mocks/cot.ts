/**
 * ─────────────────────────────  DEMO DATA  ─────────────────────────────
 * Synthetic Commitments of Traders history used only to preview the COT
 * visualisations. It is NOT CFTC data and must never be interpreted.
 */
import type { CotPositionRow, CotReport } from '@/types/analysis';
import { hashString, mulberry32 } from './random';

const CONTRACTS: Record<string, { contract: string; oi: number }> = {
  EURUSD: { contract: 'EURO FX — CME (demo)', oi: 700_000 },
  GBPUSD: { contract: 'BRITISH POUND — CME (demo)', oi: 220_000 },
  XAUUSD: { contract: 'GOLD — COMEX (demo)', oi: 500_000 },
  US30: { contract: 'DJIA x $5 — CBOT (demo)', oi: 90_000 },
};

const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export function demoCotReport(instrument: string, weeks = 208, now = Date.now()): CotReport | null {
  const meta = CONTRACTS[instrument];
  if (!meta) return null;
  const rand = mulberry32(hashString(`cot:${instrument}`));
  // Most recent Tuesday on/before now (UTC).
  const d = new Date(now);
  const back = (d.getUTCDay() + 7 - 2) % 7;
  const lastTuesday = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - back - 7);
  const rows: CotPositionRow[] = [];
  let bias = (rand() - 0.5) * 0.4;
  for (let w = weeks - 1; w >= 0; w--) {
    const report = lastTuesday - w * 7 * 86_400_000;
    bias = Math.max(-0.45, Math.min(0.45, bias * 0.97 + (rand() - 0.5) * 0.08));
    const oi = Math.round(meta.oi * (0.85 + rand() * 0.3));
    const spec = oi * 0.35;
    const comm = oi * 0.5;
    const small = oi * 0.15;
    const ncLong = Math.round(spec * (0.5 + bias));
    const ncShort = Math.round(spec * (0.5 - bias));
    const cLong = Math.round(comm * (0.5 - bias * 0.8));
    const cShort = Math.round(comm * (0.5 + bias * 0.8));
    const nrLong = Math.round(small * (0.5 + (rand() - 0.5) * 0.2));
    rows.push({
      reportDate: iso(report),
      publicationDate: iso(report + 3 * 86_400_000),
      openInterest: oi,
      commercialLong: cLong,
      commercialShort: cShort,
      nonCommercialLong: ncLong,
      nonCommercialShort: ncShort,
      nonReportableLong: nrLong,
      nonReportableShort: Math.round(small - nrLong),
    });
  }
  return {
    contract: meta.contract,
    instrument,
    reportType: 'Legacy — Futures Only (demo)',
    source: 'DEMO DATA — synthetic, not CFTC',
    updatedAt: new Date(now).toISOString(),
    history: rows,
  };
}
