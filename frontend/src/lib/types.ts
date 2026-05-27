export type Strategy = "short" | "options" | "spread";
export type RiskType = "defined" | "undefined";

export interface PortfolioRiskMetrics {
  total_exposure: number;
  total_max_loss: number;
  open_positions: number;
  max_positions: number;
  beta_to_spy: number | null;
  tickers: string[];
}

export interface PortfolioRiskSectorData {
  amount: number;
  percentage: number;
  over_limit: boolean;
}

export interface PortfolioRiskCorrelation {
  tickers: string[];
  matrix: number[][];
  window: number;
}

export interface PortfolioRiskReport {
  metrics: PortfolioRiskMetrics;
  sector_exposure: {
    sectors: Record<string, PortfolioRiskSectorData>;
    total_exposure: number;
    max_sector_pct: number;
  };
  correlation: PortfolioRiskCorrelation | null;
}

export interface SpreadLeg {
  option_type: string; // "call" | "put"
  action: string; // "buy" | "sell"
  strike: number;
  premium: number | null;
  contracts: number | null;
}

export interface Recommendation {
  id: number | null;
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
  legs: SpreadLeg[] | null;
  risk_type: RiskType;
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

export interface WatchlistItem {
  ticker: string;
  sector: string | null;
  added_at: string | null;
}

export interface WatchlistResponse {
  tickers: WatchlistItem[];
  count: number;
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

// Backtesting types

export interface BacktestConfig {
  tickers: string[] | null;
  strategy: string;
  start_date: string | null;
  end_date: string | null;
  max_position: number;
  hold_days: number;
  score_threshold: number;
  max_concurrent: number;
}

export interface BacktestTrade {
  ticker: string;
  strategy: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  return_pct: number;
  score: number;
  hit_stop: boolean;
  hit_target: boolean;
}

export interface BacktestMetrics {
  total_pnl: number;
  num_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  profit_factor: number | string;
  avg_pnl: number;
  best_trade: number;
  worst_trade: number;
  avg_return_pct: number;
  avg_hold_days: number;
  total_position_value: number;
  return_on_capital: number;
  max_concurrent_used: number;
}

export interface DailyEquityPoint {
  date: string;
  cumulative_pnl: number;
  unrealized: number;
  total_equity: number;
  open_positions: number;
}

export interface BacktestResponse {
  strategy: string;
  start_date: string;
  end_date: string;
  num_trades: number;
  metrics: BacktestMetrics;
  trades: BacktestTrade[];
  daily_equity?: DailyEquityPoint[];
}

// Alerts types

export interface AlertEntry {
  id: number;
  ticker: string;
  alert_type: string;
  message: string;
  acknowledged: boolean;
  created_at: string | null;
}

export interface AlertSetting {
  id: number;
  channel: string;
  webhook_url: string | null;
  enabled: boolean;
  score_threshold: number;
  alert_stop_loss: boolean;
  alert_target_hit: boolean;
  alert_high_conviction: boolean;
}

// Portfolio / Trading types

export interface AlpacaPosition {
  ticker: string;
  qty: number;
  side: string;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  change_today: number;
}

export interface PortfolioSummary {
  equity: number;
  buying_power: number;
  cash: number;
  day_trade_count: number;
  alpaca_positions: number;
  paper_positions: number;
  total_positions: number;
  total_market_value: number;
  total_unrealized_pl: number;
  positions: AlpacaPosition[];
  error?: string;
}

export interface AlpacaOrder {
  order_id: string;
  ticker: string;
  side: string | null;
  qty: number | null;
  type: string | null;
  status: string | null;
  filled_price: number | null;
  submitted_at: string | null;
  filled_at: string | null;
}

export interface ExecutionLogEntry {
  id: number;
  ticker: string;
  action: string;
  strategy: string | null;
  qty: number | null;
  side: string | null;
  order_id: string | null;
  reason: string | null;
  passed_safety: boolean;
  created_at: string | null;
}

export interface ExecutionResult {
  rec_id?: number;
  ticker: string;
  status: string;
  order_id?: string;
  reason?: string;
}

export type TradingMode = "disabled" | "paper" | "live";

export interface TradingSettings {
  trading_mode: TradingMode;
  auto_execute_enabled: boolean;
  min_score_threshold: number;
  max_daily_loss: number;
  max_open_positions: number;
}

export interface SafetyStatus {
  trading_mode: TradingMode;
  open_positions: number;
  max_open_positions: number;
  daily_orders: number;
  max_daily_orders: number;
  daily_loss: number;
  max_daily_loss: number;
  max_single_position: number;
  market_hours_only: boolean;
  blocked_tickers: string[];
}

export interface BacktestCompareResponse {
  strategies: Record<string, BacktestResponse>;
  summary: Record<
    string,
    {
      total_pnl: number;
      num_trades: number;
      win_rate: number;
      sharpe_ratio: number;
      max_drawdown: number;
      profit_factor: number | string;
    }
  >;
}
