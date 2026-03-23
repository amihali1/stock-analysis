import type {
  HealthStatus,
  RecommendationsResponse,
  AnalysisResponse,
  TickerListResponse,
  PaperTrade,
  PaperTradeListResponse,
  WatchlistResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function getHealth(): Promise<HealthStatus> {
  return fetchAPI<HealthStatus>("/api/health");
}

export async function getRecommendations(
  strategy?: string,
  limit = 20
): Promise<RecommendationsResponse> {
  const params = new URLSearchParams();
  if (strategy) params.set("strategy", strategy);
  params.set("limit", String(limit));
  return fetchAPI<RecommendationsResponse>(`/api/recommendations?${params}`);
}

export async function getAnalysis(
  ticker: string,
  days = 90
): Promise<AnalysisResponse> {
  return fetchAPI<AnalysisResponse>(`/api/analysis/${ticker}?days=${days}`);
}

export async function getTickers(): Promise<TickerListResponse> {
  return fetchAPI<TickerListResponse>("/api/tickers");
}

export async function getPaperTrades(
  status?: string
): Promise<PaperTradeListResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  return fetchAPI<PaperTradeListResponse>(`/api/paper-trades?${params}`);
}

export async function openPaperTrade(trade: {
  ticker: string;
  strategy: string;
  entry_price: number;
  stop_loss?: number;
  target_price?: number;
  position_size?: number;
  max_loss?: number;
  contracts?: number;
  strike?: number;
  option_type?: string;
  score?: number;
}): Promise<PaperTrade> {
  return fetchAPI<PaperTrade>("/api/paper-trades", {
    method: "POST",
    body: JSON.stringify(trade),
  });
}

export async function closePaperTrade(
  tradeId: number,
  exitPrice: number
): Promise<PaperTrade> {
  return fetchAPI<PaperTrade>(`/api/paper-trades/${tradeId}/close`, {
    method: "POST",
    body: JSON.stringify({ exit_price: exitPrice }),
  });
}

export async function getWatchlist(): Promise<WatchlistResponse> {
  return fetchAPI<WatchlistResponse>("/api/watchlist");
}

export async function addToWatchlist(
  tickers: string[]
): Promise<WatchlistResponse> {
  return fetchAPI<WatchlistResponse>("/api/watchlist", {
    method: "POST",
    body: JSON.stringify({ tickers }),
  });
}

export async function removeFromWatchlist(ticker: string): Promise<void> {
  return fetchAPI<void>(`/api/watchlist/${encodeURIComponent(ticker)}`, {
    method: "DELETE",
  });
}
