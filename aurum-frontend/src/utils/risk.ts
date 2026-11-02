export interface PositionSizeInput {
  balance: number;
  riskPct: number;
  stopPoints: number;
  pointValue: number; // account currency per point per 1.0 lot
}

/** Informational sizing maths. Not a risk control: the backend enforces limits. */
export function calculatePositionSize(v: PositionSizeInput): { riskAmount: number; lots: number } {
  const riskAmount = (v.balance * v.riskPct) / 100;
  return { riskAmount, lots: riskAmount / (v.stopPoints * v.pointValue) };
}
