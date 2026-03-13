export type Strategy = "short" | "options";

export interface Recommendation {
  ticker: string;
  date: string;
  strategy: Strategy;
  score: number;
  directional_signal: number | null;
  volatility_signal: number | null;
  sentiment_signal: number | null;
  entry_price: number | null;
  stop_loss: number | null;
  target_price: number | null;
  position_size: number | null;
  max_loss: number | null;
  contracts: number | null;
  strike: number | null;
  expiry: string | null;
  option_type: string | null;
  notes: string | null;
}

export interface RecommendationsResponse {
  recommendations: Recommendation[];
  count: number;
}

export interface HealthStatus {
  status: "ok" | "degraded";
  database: string;
  ollama: string;
  ollama_models: string[];
  last_pipeline_run: string | null;
  scheduler_jobs: Record<string, string>;
}

export interface PricePoint {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

export interface IndicatorPoint {
  date: string;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_histogram: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  sma_50: number | null;
  sma_200: number | null;
  volume_zscore: number | null;
}

export interface SentimentEntry {
  date: string;
  source: string | null;
  headline: string | null;
  sentiment: number | null;
  confidence: number | null;
  reasoning: string | null;
}

export interface TickerInfo {
  ticker: string;
  name: string | null;
  sector: string | null;
  exchange: string | null;
  latest_price_date: string | null;
  latest_close: number | null;
}

export interface TickerListResponse {
  tickers: TickerInfo[];
  count: number;
}

export interface PaperTrade {
  id: number;
  ticker: string;
  strategy: Strategy;
  status: "open" | "closed";
  entry_price: number;
  stop_loss: number | null;
  target_price: number | null;
  position_size: number | null;
  max_loss: number | null;
  contracts: number | null;
  strike: number | null;
  option_type: string | null;
  exit_price: number | null;
  pnl: number | null;
  score: number | null;
  opened_at: string | null;
  closed_at: string | null;
  current_price: number | null;
  unrealized_pnl: number | null;
}

export interface PaperTradeListResponse {
  trades: PaperTrade[];
  summary: {
    total_trades: number;
    open_trades: number;
    closed_trades: number;
    win_rate: number;
    total_pnl: number;
    avg_pnl: number;
  };
}

export interface AnalysisResponse {
  ticker: string;
  name: string | null;
  sector: string | null;
  latest_price: PricePoint | null;
  prices: PricePoint[];
  indicators: IndicatorPoint[];
  sentiments: SentimentEntry[];
  recommendations: Recommendation[];
}
