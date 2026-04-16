"use client";

import { useEffect, useState } from "react";
import SectorAllocation from "@/components/SectorAllocation";
import CorrelationHeatmap from "@/components/CorrelationHeatmap";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface PortfolioMetrics {
  total_exposure: number;
  total_max_loss: number;
  open_positions: number;
  max_positions: number;
  beta_to_spy: number | null;
  tickers: string[];
}

interface SectorData {
  amount: number;
  percentage: number;
  over_limit: boolean;
}

interface CorrelationData {
  tickers: string[];
  matrix: number[][];
  window: number;
}

interface RiskReport {
  metrics: PortfolioMetrics;
  sector_exposure: {
    sectors: Record<string, SectorData>;
    total_exposure: number;
    max_sector_pct: number;
  };
  correlation: CorrelationData | null;
}

function metricCard(label: string, value: string, color = "text-white") {
  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="text-gray-500 text-xs uppercase tracking-wider">
        {label}
      </div>
      <div className={`text-xl font-bold font-mono mt-1 ${color}`}>
        {value}
      </div>
    </div>
  );
}

export default function PortfolioPage() {
  const [report, setReport] = useState<RiskReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/portfolio/risk`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setReport)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="text-gray-500 text-center py-20">
        Computing risk metrics...
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-red-400 bg-red-900/20 border border-red-800 rounded p-4">
        Failed to load risk report: {error}
      </div>
    );
  }

  if (!report) return null;

  const { metrics, sector_exposure, correlation } = report;
  const utilizationPct =
    metrics.max_positions > 0
      ? ((metrics.open_positions / metrics.max_positions) * 100).toFixed(0)
      : "0";

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Portfolio Risk</h1>

      {/* Metric Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
        {metricCard(
          "Total Exposure",
          `$${metrics.total_exposure.toLocaleString(undefined, {
            maximumFractionDigits: 0,
          })}`
        )}
        {metricCard(
          "Total Max Loss",
          `$${metrics.total_max_loss.toLocaleString(undefined, {
            maximumFractionDigits: 0,
          })}`,
          metrics.total_max_loss > 0 ? "text-red-400" : "text-white"
        )}
        {metricCard(
          "Open Positions",
          `${metrics.open_positions} / ${metrics.max_positions}`,
          parseInt(utilizationPct) > 80 ? "text-yellow-400" : "text-white"
        )}
        {metricCard(
          "Beta to SPY",
          metrics.beta_to_spy !== null ? metrics.beta_to_spy.toFixed(2) : "N/A",
          metrics.beta_to_spy !== null && Math.abs(metrics.beta_to_spy) > 1.5
            ? "text-yellow-400"
            : "text-white"
        )}
        {metricCard(
          "Position Utilization",
          `${utilizationPct}%`,
          parseInt(utilizationPct) > 80 ? "text-yellow-400" : "text-green-400"
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Sector Allocation */}
        <div className="bg-gray-900 rounded-lg p-4">
          <h2 className="text-lg font-semibold mb-4">Sector Allocation</h2>
          <SectorAllocation
            sectors={sector_exposure.sectors}
            totalExposure={sector_exposure.total_exposure}
            maxSectorPct={sector_exposure.max_sector_pct}
          />
        </div>

        {/* Correlation Heatmap */}
        <div className="bg-gray-900 rounded-lg p-4">
          <h2 className="text-lg font-semibold mb-4">
            Correlation Matrix
            {correlation && (
              <span className="text-gray-500 text-xs font-normal ml-2">
                ({correlation.window}-day window)
              </span>
            )}
          </h2>
          {correlation ? (
            <CorrelationHeatmap
              tickers={correlation.tickers}
              matrix={correlation.matrix}
            />
          ) : (
            <div className="text-gray-500 text-center py-10 text-sm">
              Need at least 2 open positions for correlation analysis
            </div>
          )}
        </div>
      </div>

      {/* Open Position Tickers */}
      {metrics.tickers.length > 0 && (
        <div className="mt-6 bg-gray-900 rounded-lg p-4">
          <h2 className="text-lg font-semibold mb-3">Open Positions</h2>
          <div className="flex flex-wrap gap-2">
            {metrics.tickers.map((ticker) => (
              <a
                key={ticker}
                href={`/analysis/${ticker}`}
                className="bg-gray-800 hover:bg-gray-700 border border-gray-700 text-sm font-mono px-3 py-1 rounded transition-colors"
              >
                {ticker}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
