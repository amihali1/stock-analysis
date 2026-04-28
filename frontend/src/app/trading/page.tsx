"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getPortfolioSummary,
  getPortfolioOrders,
  closePosition,
  emergencyCloseAll,
  triggerPortfolioSync,
} from "@/lib/api";
import type { PortfolioSummary, AlpacaOrder } from "@/lib/types";
import TradingControls from "@/components/TradingControls";

function MetricCard({
  label,
  value,
  color = "text-white",
}: {
  label: string;
  value: string;
  color?: string;
}) {
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

function StatusBadge({ status }: { status: string | null }) {
  const colors: Record<string, string> = {
    filled: "bg-green-900 text-green-300",
    partially_filled: "bg-yellow-900 text-yellow-300",
    new: "bg-blue-900 text-blue-300",
    accepted: "bg-blue-900 text-blue-300",
    canceled: "bg-gray-700 text-gray-400",
    rejected: "bg-red-900 text-red-300",
    expired: "bg-gray-700 text-gray-400",
  };
  const cls = colors[status || ""] || "bg-gray-700 text-gray-400";
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-mono ${cls}`}>
      {status || "unknown"}
    </span>
  );
}

export default function TradingPage() {
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [orders, setOrders] = useState<AlpacaOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [confirmEmergency, setConfirmEmergency] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, o] = await Promise.all([
        getPortfolioSummary(),
        getPortfolioOrders(30),
      ]);
      setSummary(s);
      setOrders(o);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await triggerPortfolioSync();
      await refresh();
    } finally {
      setSyncing(false);
    }
  };

  const handleClose = async (ticker: string) => {
    if (!confirm(`Close ${ticker} position?`)) return;
    await closePosition(ticker);
    await refresh();
  };

  const handleEmergencyClose = async () => {
    if (!confirmEmergency) {
      setConfirmEmergency(true);
      return;
    }
    await emergencyCloseAll();
    setConfirmEmergency(false);
    await refresh();
  };

  if (loading) {
    return (
      <div className="text-gray-500 text-center py-20">
        Loading portfolio...
      </div>
    );
  }

  if (error && !summary) {
    return (
      <div className="text-red-400 bg-red-900/20 border border-red-800 rounded p-4">
        {error}
      </div>
    );
  }

  const hasPositions = summary && summary.positions.length > 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Trading Portfolio</h1>
        <div className="flex gap-2">
          <button
            onClick={handleSync}
            disabled={syncing}
            className="bg-gray-800 hover:bg-gray-700 text-sm px-3 py-1.5 rounded border border-gray-700 transition-colors disabled:opacity-50"
          >
            {syncing ? "Syncing..." : "Sync"}
          </button>
          <button
            onClick={handleEmergencyClose}
            className={`text-sm px-3 py-1.5 rounded border transition-colors ${
              confirmEmergency
                ? "bg-red-700 border-red-600 text-white animate-pulse"
                : "bg-red-900/30 border-red-800 text-red-400 hover:bg-red-900/50"
            }`}
          >
            {confirmEmergency ? "Click again to confirm" : "Emergency Close All"}
          </button>
        </div>
      </div>

      {/* Account Summary */}
      {summary && !summary.error && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <MetricCard
            label="Equity"
            value={`$${summary.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          />
          <MetricCard
            label="Buying Power"
            value={`$${summary.buying_power.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          />
          <MetricCard
            label="Cash"
            value={`$${summary.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          />
          <MetricCard
            label="Unrealized P&L"
            value={`$${summary.total_unrealized_pl.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
            color={summary.total_unrealized_pl >= 0 ? "text-green-400" : "text-red-400"}
          />
          <MetricCard
            label="Positions"
            value={`${summary.alpaca_positions} live / ${summary.paper_positions} paper`}
          />
          <MetricCard
            label="Day Trades"
            value={String(summary.day_trade_count)}
            color={summary.day_trade_count >= 3 ? "text-yellow-400" : "text-white"}
          />
        </div>
      )}

      {summary?.error && (
        <div className="bg-yellow-900/20 border border-yellow-800 rounded p-4 mb-6 text-yellow-300 text-sm">
          Alpaca not connected: {summary.error}
        </div>
      )}

      {/* Trading Controls */}
      <div className="mb-6">
        <TradingControls />
      </div>

      {/* Open Positions */}
      <div className="bg-gray-900 rounded-lg p-4 mb-6">
        <h2 className="text-lg font-semibold mb-4">Open Positions</h2>
        {hasPositions ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs uppercase border-b border-gray-800">
                  <th className="text-left py-2 pr-4">Ticker</th>
                  <th className="text-right py-2 pr-4">Qty</th>
                  <th className="text-left py-2 pr-4">Side</th>
                  <th className="text-right py-2 pr-4">Entry</th>
                  <th className="text-right py-2 pr-4">Current</th>
                  <th className="text-right py-2 pr-4">Market Value</th>
                  <th className="text-right py-2 pr-4">P&L</th>
                  <th className="text-right py-2 pr-4">% Change</th>
                  <th className="text-right py-2"></th>
                </tr>
              </thead>
              <tbody>
                {summary!.positions.map((p) => (
                  <tr
                    key={p.ticker}
                    className="border-b border-gray-800/50 hover:bg-gray-800/30"
                  >
                    <td className="py-2 pr-4 font-mono font-bold">{p.ticker}</td>
                    <td className="text-right py-2 pr-4 font-mono">{p.qty}</td>
                    <td className="py-2 pr-4">
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          p.side === "long"
                            ? "bg-green-900 text-green-300"
                            : "bg-red-900 text-red-300"
                        }`}
                      >
                        {p.side}
                      </span>
                    </td>
                    <td className="text-right py-2 pr-4 font-mono">
                      ${p.avg_entry_price.toFixed(2)}
                    </td>
                    <td className="text-right py-2 pr-4 font-mono">
                      ${p.current_price.toFixed(2)}
                    </td>
                    <td className="text-right py-2 pr-4 font-mono">
                      ${Math.abs(p.market_value).toLocaleString(undefined, {
                        maximumFractionDigits: 0,
                      })}
                    </td>
                    <td
                      className={`text-right py-2 pr-4 font-mono ${
                        p.unrealized_pl >= 0 ? "text-green-400" : "text-red-400"
                      }`}
                    >
                      {p.unrealized_pl >= 0 ? "+" : ""}$
                      {p.unrealized_pl.toFixed(2)}
                    </td>
                    <td
                      className={`text-right py-2 pr-4 font-mono ${
                        p.change_today >= 0 ? "text-green-400" : "text-red-400"
                      }`}
                    >
                      {(p.change_today * 100).toFixed(2)}%
                    </td>
                    <td className="text-right py-2">
                      <button
                        onClick={() => handleClose(p.ticker)}
                        className="text-xs text-red-400 hover:text-red-300 border border-red-800 px-2 py-0.5 rounded"
                      >
                        Close
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-gray-500 text-center py-8 text-sm">
            No open positions
          </div>
        )}
      </div>

      {/* Order History */}
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-lg font-semibold mb-4">Recent Orders</h2>
        {orders.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs uppercase border-b border-gray-800">
                  <th className="text-left py-2 pr-4">Ticker</th>
                  <th className="text-left py-2 pr-4">Side</th>
                  <th className="text-right py-2 pr-4">Qty</th>
                  <th className="text-left py-2 pr-4">Type</th>
                  <th className="text-left py-2 pr-4">Status</th>
                  <th className="text-right py-2 pr-4">Filled Price</th>
                  <th className="text-left py-2">Submitted</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr
                    key={o.order_id}
                    className="border-b border-gray-800/50 hover:bg-gray-800/30"
                  >
                    <td className="py-2 pr-4 font-mono font-bold">{o.ticker}</td>
                    <td className="py-2 pr-4">{o.side}</td>
                    <td className="text-right py-2 pr-4 font-mono">{o.qty}</td>
                    <td className="py-2 pr-4">{o.type}</td>
                    <td className="py-2 pr-4">
                      <StatusBadge status={o.status} />
                    </td>
                    <td className="text-right py-2 pr-4 font-mono">
                      {o.filled_price ? `$${o.filled_price.toFixed(2)}` : "-"}
                    </td>
                    <td className="py-2 text-gray-500 text-xs">
                      {o.submitted_at
                        ? new Date(o.submitted_at).toLocaleString()
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-gray-500 text-center py-8 text-sm">
            No orders yet
          </div>
        )}
      </div>
    </div>
  );
}
