const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://10.0.0.47:8000";

async function fetchAPI<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function getHealth() {
  return fetchAPI<import("./types").HealthStatus>("/api/health");
}

export async function getRecommendations(strategy?: string, limit = 10) {
  const params = new URLSearchParams();
  if (strategy) params.set("strategy", strategy);
  params.set("limit", String(limit));
  return fetchAPI<{ recommendations: import("./types").Recommendation[] }>(
    `/api/recommendations?${params}`
  );
}

export async function getAnalysis(ticker: string) {
  return fetchAPI<import("./types").StockAnalysis>(`/api/analysis/${ticker}`);
}
