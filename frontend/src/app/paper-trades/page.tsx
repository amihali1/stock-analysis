"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getPaperTrades } from "@/lib/api";
import type { PaperTrade, PaperTradeListResponse } from "@/lib/types";

function pnlColor(val: number | null): string {
  if (val === null) return "text-gray-400";
  if (val > 0) return "text-green-400";
  if (val < 0) return "text-red-400";
  return "text-gray-400";
}

function dollar(val: number | null): string {
  if (val === null) return "—";
  const sign = val >= 0 ? "+" : "";
  return `${sign}$${val.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function PaperTradesPage() {
  const [data, setData] = useState<PaperTradeListResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const result = await getPaperTrades();
        setData(result);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return <div className="text-gray-500 text-center py-20">Loading paper trades...</div>;
  }

  const trades = data?.trades ?? [];
  const summary = data?.summary;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Paper Trading</h1>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          <StatCard label="Open Trades" value={String(summary.open_trades)} />
          <StatCard label="Closed Trades" value={String(summary.closed_trades)} />
          <StatCard
            label="Win Rate"
            value={`${(summary.win_rate * 100).toFixed(0)}%`}
            color={summary.win_rate >= 0.5 ? "text-green-400" : "text-red-400"}
          />
          <StatCard
            label="Total P&L"
            value={dollar(summary.total_pnl)}
            color={pnlColor(summary.total_pnl)}
          />
        </div>
      )}

      {trades.length === 0 ? (
        <div className="text-gray-500 text-center py-20">
          No paper trades yet. Take a trade from the{" "}
          <Link href="/" className="text-blue-400 hover:underline">
            dashboard
          </Link>
          .
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-800 text-left">
                <th className="py-3 px-3">Ticker</th>
                <th className="py-3 px-3">Strategy</th>
                <th className="py-3 px-3">Status</th>
                <th className="py-3 px-3 text-right">Entry</th>
                <th className="py-3 px-3 text-right">Current / Exit</th>
                <th className="py-3 px-3 text-right">P&L</th>
                <th className="py-3 px-3 text-right">Position</th>
                <th className="py-3 px-3">Opened</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade) => (
                <tr
                  key={trade.id}
                  className="border-b border-gray-800/50 hover:bg-gray-900/50"
                >
                  <td className="py-3 px-3">
                    <Link
                      href={`/analysis/${trade.ticker}`}
                      className="font-mono font-bold hover:text-blue-400"
                    >
                      {trade.ticker}
                    </Link>
                  </td>
                  <td className="py-3 px-3">
                    <span
                      className={`text-xs px-2 py-0.5 rounded border ${
                        trade.strategy === "short"
                          ? "bg-red-900/50 text-red-300 border-red-800"
                          : "bg-purple-900/50 text-purple-300 border-purple-800"
                      }`}
                    >
                      {trade.strategy}
                    </span>
                  </td>
                  <td className="py-3 px-3">
                    <span
                      className={`text-xs ${
                        trade.status === "open"
                          ? "text-blue-400"
                          : "text-gray-500"
                      }`}
                    >
                      {trade.status}
                    </span>
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    ${trade.entry_price.toFixed(2)}
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    {trade.status === "open"
                      ? trade.current_price
                        ? `$${trade.current_price.toFixed(2)}`
                        : "—"
                      : trade.exit_price
                        ? `$${trade.exit_price.toFixed(2)}`
                        : "—"}
                  </td>
                  <td
                    className={`py-3 px-3 text-right font-mono font-bold ${pnlColor(
                      trade.status === "open"
                        ? trade.unrealized_pnl
                        : trade.pnl
                    )}`}
                  >
                    {dollar(
                      trade.status === "open"
                        ? trade.unrealized_pnl
                        : trade.pnl
                    )}
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    {trade.position_size
                      ? `$${trade.position_size.toLocaleString()}`
                      : "—"}
                  </td>
                  <td className="py-3 px-3 text-gray-500 text-xs">
                    {trade.opened_at
                      ? new Date(trade.opened_at).toLocaleDateString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatCard({
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
