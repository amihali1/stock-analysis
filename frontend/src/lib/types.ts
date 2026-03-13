export type Strategy = "short" | "options";

export interface Recommendation {
  id: number;
  ticker: string;
  date: string;
  strategy: Strategy;
  score: number;
  directional_signal: number | null;
  volatility_signal: number | null;
  sentiment_signal: number | null;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  position_size: number;
  max_loss: number;
  contracts: number | null;
  strike: number | null;
  expiry: string | null;
  option_type: string | null;
  notes: string | null;
}

export interface HealthStatus {
  status: "ok" | "degraded";
  database: string;
  ollama: string;
  ollama_models?: string[];
}

export interface StockAnalysis {
  ticker: string;
  // Expanded in P4-004
}
