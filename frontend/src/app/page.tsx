"use client";

import React, { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  executeRecommendation,
  getRecommendations,
  getTradingSettings,
} from "@/lib/api";
import type {
  Recommendation,
  Strategy,
  TradingMode,
} from "@/lib/types";
import { formatOptionLeg, formatStockLeg } from "@/lib/legs";

function scoreColor(score: number): string {
  if (score >= 0.7) return "text-green-400";
  if (score >= 0.5) return "text-yellow-400";
  return "text-gray-400";
}

function strategyBadge(strategy: Strategy) {
  const cls =
    strategy === "short"
      ? "bg-red-900/50 text-red-300 border-red-800"
      : strategy === "pair_short"
      ? "bg-cyan-900/50 text-cyan-300 border-cyan-800"
      : strategy === "long"
      ? "bg-green-900/50 text-green-300 border-green-800"
      : strategy === "spread" || strategy === "bull_spread"
      ? "bg-blue-900/50 text-blue-300 border-blue-800"
      : "bg-purple-900/50 text-purple-300 border-purple-800";
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${cls}`}>
      {strategy}
    </span>
  );
}

function riskBadge(riskType: string) {
  if (riskType === "defined") {
    return (
      <span className="text-xs px-2 py-0.5 rounded border bg-green-900/50 text-green-300 border-green-800">
        Defined
      </span>
    );
  }
  return (
    <span className="text-xs px-2 py-0.5 rounded border bg-yellow-900/50 text-yellow-300 border-yellow-800">
      Undefined
    </span>
  );
}

function formatDollars(val: number | null): string {
  if (val === null) return "—";
  return `$${val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

type SortKey = "score" | "ticker" | "max_loss" | "position_size";

function hasOptionDetail(rec: Recommendation): boolean {
  if (rec.legs && rec.legs.length > 0) return true;
  if (rec.stock_legs && rec.stock_legs.length > 0) return true;
  if (rec.strike !== null && rec.option_type !== null) return true;
  return false;
}

function OptionDetailRow({
  rec,
  colSpan,
}: {
  rec: Recommendation;
  colSpan: number;
}) {
  // Multi-leg spread: render each leg on its own line.
  if (rec.legs && rec.legs.length > 0) {
    return (
      <tr className="border-b border-gray-800/50 bg-gray-950/40">
        <td colSpan={colSpan} className="py-2 px-3">
          <div className="pl-6 text-xs font-mono text-gray-400 space-y-0.5">
            {rec.legs.map((leg, j) => (
              <div key={j}>
                <span className="text-gray-500">↳ </span>
                {formatOptionLeg(leg, rec.expiry)}
              </div>
            ))}
          </div>
        </td>
      </tr>
    );
  }
  // Pair trade: short leg + hedge leg.
  if (rec.stock_legs && rec.stock_legs.length > 0) {
    return (
      <tr className="border-b border-gray-800/50 bg-gray-950/40">
        <td colSpan={colSpan} className="py-2 px-3">
          <div className="pl-6 text-xs font-mono text-gray-400 space-y-0.5">
            {rec.stock_legs.map((leg, j) => (
              <div key={j}>
                <span className="text-gray-500">↳ </span>
                {formatStockLeg(leg)}
              </div>
            ))}
          </div>
        </td>
      </tr>
    );
  }
  // Single-leg option: derive a one-line summary from rec fields directly.
  const action = rec.strategy === "short" ? "SELL" : "BUY";
  const qty = rec.contracts ?? "?";
  const exp = rec.expiry ? ` ${rec.expiry}` : "";
  const strike = rec.strike !== null ? `$${rec.strike}` : "?";
  const type = (rec.option_type || "").toUpperCase();
  return (
    <tr className="border-b border-gray-800/50 bg-gray-950/40">
      <td colSpan={colSpan} className="py-2 px-3">
        <div className="pl-6 text-xs font-mono text-gray-400">
          <span className="text-gray-500">↳ </span>
          {action} {qty}×{exp} {strike} {type}
        </div>
      </td>
    </tr>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<div className="text-gray-500 text-center py-20">Loading...</div>}>
      <Dashboard />
    </Suspense>
  );
}

function Dashboard() {
  const searchParams = useSearchParams();
  const strategyFilter = searchParams.get("strategy") as Strategy | null;

  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortAsc, setSortAsc] = useState(false);
  const [tradingMode, setTradingMode] = useState<TradingMode>("disabled");
  const [executing, setExecuting] = useState<number | null>(null);
  const [execMessage, setExecMessage] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [data, settings] = await Promise.all([
        getRecommendations(strategyFilter || undefined, 50),
        getTradingSettings().catch(() => null),
      ]);
      setRecs(data.recommendations);
      if (settings) setTradingMode(settings.trading_mode);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [strategyFilter]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const handleExecute = async (rec: Recommendation) => {
    if (!rec.id) return;
    if (
      !confirm(
        `Execute ${rec.strategy.toUpperCase()} on ${rec.ticker} (${tradingMode} mode)?`
      )
    ) {
      return;
    }
    setExecuting(rec.id);
    setExecMessage(null);
    try {
      const result = await executeRecommendation(rec.id);
      const msg =
        result.status === "submitted"
          ? `Submitted ${rec.ticker} — order ${result.order_id ?? ""}`
          : `${rec.ticker}: ${result.status}${result.reason ? " — " + result.reason : ""}`;
      setExecMessage(msg);
    } catch (e) {
      setExecMessage(
        e instanceof Error ? `Execute failed: ${e.message}` : "Execute failed"
      );
    } finally {
      setExecuting(null);
    }
  };

  const tradingEnabled = tradingMode !== "disabled";

  const sorted = [...recs].sort((a, b) => {
    let cmp = 0;
    if (sortKey === "ticker") cmp = a.ticker.localeCompare(b.ticker);
    else cmp = ((a[sortKey] ?? 0) as number) - ((b[sortKey] ?? 0) as number);
    return sortAsc ? cmp : -cmp;
  });

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortAsc ? " ▲" : " ▼") : "";

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">
          {strategyFilter
            ? `${strategyFilter.charAt(0).toUpperCase() + strategyFilter.slice(1)} Recommendations`
            : "Top Recommendations"}
        </h1>
        <div className="flex gap-2">
          <FilterTab href="/" active={!strategyFilter} label="All" />
          <FilterTab
            href="/?strategy=short"
            active={strategyFilter === "short"}
            label="Shorts"
          />
          <FilterTab
            href="/?strategy=options"
            active={strategyFilter === "options"}
            label="Options"
          />
          <FilterTab
            href="/?strategy=spread"
            active={strategyFilter === "spread"}
            label="Spreads"
          />
        </div>
      </div>

      {execMessage && (
        <div className="mb-4 text-sm bg-blue-900/20 border border-blue-800 rounded p-3 text-blue-200 flex items-center justify-between">
          <span>{execMessage}</span>
          <button
            onClick={() => setExecMessage(null)}
            className="text-xs text-blue-300 hover:text-white"
          >
            dismiss
          </button>
        </div>
      )}

      {loading && recs.length === 0 && (
        <div className="text-gray-500 text-center py-20">Loading recommendations...</div>
      )}

      {error && (
        <div className="text-red-400 bg-red-900/20 border border-red-800 rounded p-4 mb-4">
          {error}
        </div>
      )}

      {!loading && recs.length === 0 && !error && (
        <div className="text-gray-500 text-center py-20">
          No recommendations yet. Run the pipeline to generate signals.
        </div>
      )}

      {sorted.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-800 text-left">
                <th
                  className="py-3 px-3 cursor-pointer hover:text-white"
                  onClick={() => handleSort("ticker")}
                >
                  Ticker{sortIcon("ticker")}
                </th>
                <th className="py-3 px-3">Strategy</th>
                <th className="py-3 px-3">Risk</th>
                <th
                  className="py-3 px-3 cursor-pointer hover:text-white text-right"
                  onClick={() => handleSort("score")}
                >
                  Score{sortIcon("score")}
                </th>
                <th className="py-3 px-3 text-right">Sentiment</th>
                <th className="py-3 px-3 text-right">Entry</th>
                <th className="py-3 px-3 text-right">Stop Loss</th>
                <th
                  className="py-3 px-3 cursor-pointer hover:text-white text-right"
                  onClick={() => handleSort("position_size")}
                >
                  Position{sortIcon("position_size")}
                </th>
                <th
                  className="py-3 px-3 cursor-pointer hover:text-white text-right"
                  onClick={() => handleSort("max_loss")}
                >
                  Max Loss{sortIcon("max_loss")}
                </th>
                {tradingEnabled && <th className="py-3 px-3"></th>}
              </tr>
            </thead>
            <tbody>
              {sorted.map((rec, i) => {
                const baseCols = 9; // ticker, strategy, risk, score, sent, entry, stop, pos, max_loss
                const colSpan = baseCols + (tradingEnabled ? 1 : 0);
                return (
                  <React.Fragment key={`${rec.ticker}-${rec.strategy}-${i}`}>
                <tr
                  className="border-b border-gray-800/50 hover:bg-gray-900/50 transition-colors"
                >
                  <td className="py-3 px-3 font-mono font-bold">
                    <Link
                      href={`/analysis/${rec.ticker}`}
                      className="text-white hover:text-blue-400"
                    >
                      {rec.ticker}
                    </Link>
                  </td>
                  <td className="py-3 px-3">{strategyBadge(rec.strategy)}</td>
                  <td className="py-3 px-3">{riskBadge(rec.risk_type)}</td>
                  <td
                    className={`py-3 px-3 text-right font-mono font-bold ${scoreColor(rec.score)}`}
                  >
                    {rec.score.toFixed(2)}
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    {rec.sentiment_signal?.toFixed(2) ?? "—"}
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    {rec.entry_price ? `$${rec.entry_price.toFixed(2)}` : "—"}
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    {rec.stop_loss ? `$${rec.stop_loss.toFixed(2)}` : "—"}
                  </td>
                  <td className="py-3 px-3 text-right font-mono">
                    {formatDollars(rec.position_size)}
                  </td>
                  <td className="py-3 px-3 text-right font-mono text-red-400">
                    {formatDollars(rec.max_loss)}
                  </td>
                  {tradingEnabled && (
                    <td className="py-3 px-3 text-right">
                      <button
                        disabled={!rec.id || executing === rec.id}
                        onClick={() => handleExecute(rec)}
                        className={`text-xs px-2 py-1 rounded border transition-colors ${
                          tradingMode === "live"
                            ? "border-red-700 text-red-300 hover:bg-red-900/30 disabled:opacity-50"
                            : "border-yellow-700 text-yellow-300 hover:bg-yellow-900/30 disabled:opacity-50"
                        }`}
                        title={`Execute in ${tradingMode} mode`}
                      >
                        {executing === rec.id ? "…" : "Execute"}
                      </button>
                    </td>
                  )}
                </tr>
                {hasOptionDetail(rec) && (
                  <OptionDetailRow rec={rec} colSpan={colSpan} />
                )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FilterTab({
  href,
  active,
  label,
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  return (
    <Link
      href={href}
      className={`px-3 py-1.5 rounded text-sm transition-colors ${
        active
          ? "bg-blue-600 text-white"
          : "bg-gray-800 text-gray-400 hover:text-white"
      }`}
    >
      {label}
    </Link>
  );
}
