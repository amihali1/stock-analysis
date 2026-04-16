import type {
  HealthStatus,
  RecommendationsResponse,
  AnalysisResponse,
  TickerListResponse,
  PaperTrade,
  PaperTradeListResponse,
  WatchlistResponse,
  BacktestConfig,
  BacktestResponse,
  BacktestCompareResponse,
  AlertEntry,
  AlertSetting,
  PortfolioSummary,
  AlpacaOrder,
  ExecutionLogEntry,
  ExecutionResult,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

export function setTokens(access: string, refresh: string) {
  localStorage.setItem("access_token", access);
  localStorage.setItem("refresh_token", refresh);
}

export function clearTokens() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options?.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (res.status === 401) {
    // Try refresh
    const refreshToken = localStorage.getItem("refresh_token");
    if (refreshToken) {
      const refreshRes = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (refreshRes.ok) {
        const data = await refreshRes.json();
        setTokens(data.access_token, data.refresh_token);
        // Retry original request with new token
        headers["Authorization"] = `Bearer ${data.access_token}`;
        const retryRes = await fetch(`${API_BASE}${path}`, {
          ...options,
          headers,
        });
        if (!retryRes.ok) {
          throw new Error(`API error: ${retryRes.status} ${retryRes.statusText}`);
        }
        return retryRes.json();
      }
    }
    clearTokens();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new Error("Session expired");
  }

  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function login(
  username: string,
  password: string
): Promise<{ access_token: string; refresh_token: string }> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Login failed");
  }
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return data;
}

export async function logout(): Promise<void> {
  try {
    await fetchAPI("/api/auth/logout", { method: "POST" });
  } finally {
    clearTokens();
  }
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

export async function runBacktest(
  config: BacktestConfig
): Promise<BacktestResponse> {
  return fetchAPI<BacktestResponse>("/api/backtest", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function compareStrategies(
  config: Omit<BacktestConfig, "strategy">
): Promise<BacktestCompareResponse> {
  return fetchAPI<BacktestCompareResponse>("/api/backtest/compare", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

// Alerts

export async function getAlerts(
  acknowledged?: boolean
): Promise<AlertEntry[]> {
  const params = new URLSearchParams();
  if (acknowledged !== undefined) params.set("acknowledged", String(acknowledged));
  return fetchAPI<AlertEntry[]>(`/api/alerts?${params}`);
}

export async function acknowledgeAlert(alertId: number): Promise<void> {
  return fetchAPI<void>(`/api/alerts/${alertId}/acknowledge`, {
    method: "POST",
  });
}

export async function acknowledgeAllAlerts(): Promise<{ count: number }> {
  return fetchAPI<{ count: number }>("/api/alerts/acknowledge-all", {
    method: "POST",
  });
}

export async function getUnreadAlertCount(): Promise<{ count: number }> {
  return fetchAPI<{ count: number }>("/api/alerts/unread-count");
}

export async function getAlertSettings(): Promise<AlertSetting[]> {
  return fetchAPI<AlertSetting[]>("/api/alert-settings");
}

export async function saveAlertSetting(setting: {
  channel: string;
  webhook_url?: string;
  bot_token?: string;
  chat_id?: string;
  enabled?: boolean;
  score_threshold?: number;
  alert_stop_loss?: boolean;
  alert_target_hit?: boolean;
  alert_high_conviction?: boolean;
}): Promise<void> {
  return fetchAPI<void>("/api/alert-settings", {
    method: "POST",
    body: JSON.stringify(setting),
  });
}

export async function deleteAlertSetting(settingId: number): Promise<void> {
  return fetchAPI<void>(`/api/alert-settings/${settingId}`, {
    method: "DELETE",
  });
}

export async function testAlertSetting(
  settingId: number
): Promise<{ status: string }> {
  return fetchAPI<{ status: string }>(
    `/api/alert-settings/${settingId}/test`,
    { method: "POST" }
  );
}

// Portfolio & Trading

export async function getPortfolioSummary(): Promise<PortfolioSummary> {
  return fetchAPI<PortfolioSummary>("/api/portfolio");
}

export async function getPortfolioOrders(
  limit = 50
): Promise<AlpacaOrder[]> {
  return fetchAPI<AlpacaOrder[]>(`/api/portfolio/orders?limit=${limit}`);
}

export async function triggerPortfolioSync(): Promise<{
  synced_positions: number;
  new_orders: number;
  equity: number;
}> {
  return fetchAPI("/api/portfolio/sync", { method: "POST" });
}

export async function executeRecommendation(
  recId: number
): Promise<ExecutionResult> {
  return fetchAPI<ExecutionResult>(
    `/api/execute/recommendation/${recId}`,
    { method: "POST" }
  );
}

export async function closePosition(
  ticker: string
): Promise<{ ticker: string; status: string; order_id?: string }> {
  return fetchAPI(`/api/execute/close/${encodeURIComponent(ticker)}`, {
    method: "POST",
  });
}

export async function emergencyCloseAll(): Promise<{
  canceled_orders: number;
  closing_positions: number;
}> {
  return fetchAPI("/api/execute/emergency-close", { method: "POST" });
}

export async function getExecutionLog(
  limit = 50
): Promise<ExecutionLogEntry[]> {
  return fetchAPI<ExecutionLogEntry[]>(`/api/execute/log?limit=${limit}`);
}
