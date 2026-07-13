import type { SpreadLeg, StockLeg } from "./types";

/** "BUY 1× 2026-08-14 $445 CALL @ $25.58" */
export function formatOptionLeg(leg: SpreadLeg, expiry: string | null): string {
  const action = leg.action.toUpperCase();
  const qty = leg.contracts ?? "?";
  const exp = expiry ? ` ${expiry}` : "";
  const strike = `$${leg.strike}`;
  const type = leg.option_type.toUpperCase();
  const premium =
    leg.premium !== null && leg.premium !== undefined
      ? ` @ $${leg.premium.toFixed(2)}`
      : "";
  return `${action} ${qty}×${exp} ${strike} ${type}${premium}`;
}

/** "SHORT 15× RIVN @ $17.48" / "HEDGE 0.3473× SPY @ $754.95" */
export function formatStockLeg(leg: StockLeg): string {
  const action = leg.leg.toUpperCase();
  const qty = Number.isInteger(leg.qty) ? leg.qty : leg.qty.toFixed(4);
  return `${action} ${qty}× ${leg.ticker} @ $${leg.entry.toFixed(2)}`;
}
