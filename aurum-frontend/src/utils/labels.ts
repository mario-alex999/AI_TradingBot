/** Reads an API identifier as words: "liquidity_sweep_reversal", "orderBlockContinuation" and "NO_TRADE" alike. */
export function humanize(id: string): string {
  const words = id.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
