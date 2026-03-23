"use client";

import { useEffect, useState, useMemo } from "react";
import { runBacktest, getWatchlist } from "@/lib/api";
import EquityCurveChart from "@/components/EquityCurveChart";
import type {
  BacktestConfig,
  BacktestResponse,
  BacktestTrade,
  WatchlistItem,
} from "@/lib/types";

type SortKey = "entry_date" | "ticker" | "pnl" | "return_pct";
type SortDir = "asc" | "desc";

function pnlColor(val: number): string {
  if (val > 0) return "text-green-400";
  if (val < 0) return "text-red-400";
  return "text-gray-400";
}

function dollar(val: number): string {
  const sign = val >= 0 ? "+" : "";
  return `${sign}$${val.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function pct(val: number): string {
  const sign = val >= 0 ? "+" : "";
  return `${sign}${(val * 100).toFixed(2)}%`;
}

function formatMetric(
  label: string,
  value: number | string | undefined
): string {
  if (value === undefined) return "—";
  if (typeof value === "string") return value === "inf" ? "Inf" : value;
  switch (label) {
    case "Total P&L":
    case "Avg P&L":
    case "Best Trade":
    case "Worst Trade":
      return dollar(value);
    case "Win Rate":
    case "Max Drawdown":
    case "Return on Capital":
      return `${(value * 100).toFixed(1)}%`;
    case "Sharpe Ratio":
      return value.toFixed(2);
    case "Profit Factor":
      return value === Infinity ? "Inf" : value.toFixed(2);
    default:
      return String(value);
  }
}

export default function BacktestPage() {
  // Watchlist tickers for the multi-select
  const [watchlistTickers, setWatchlistTickers] = useState<string[]>([]);
  const [selectedTickers, setSelectedTickers] = useState<string[]>([]);
  const [allSelected, setAllSelected] = useState(true);

  // Config form state
  const [strategy, setStrategy] = useState("combined");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [holdDays, setHoldDays] = useState(5);
  const [scoreThreshold, setScoreThreshold] = useState(0.5);
  const [maxConcurrent, setMaxConcurrent] = useState(10);
  const [maxPosition, setMaxPosition] = useState(5000);

  // Results
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sort state for trade table
  const [sortKey, setSortKey] = useState<SortKey>("entry_date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Load watchlist tickers
  useEffect(() => {
    getWatchlist()
      .then((res) => {
        setWatchlistTickers(res.tickers.map((t: WatchlistItem) => t.ticker));
      })
      .catch(() => {});
  }, []);

  async function handleRun() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const config: BacktestConfig = {
        tickers: allSelected ? null : selectedTickers,
        strategy,
        start_date: startDate || null,
        end_date: endDate || null,
        max_position: maxPosition,
        hold_days: holdDays,
        score_threshold: scoreThreshold,
        max_concurrent: maxConcurrent,
      };
      const res = await runBacktest(config);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backtest failed");
    } finally {
      setLoading(false);
    }
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir(key === "pnl" || key === "return_pct" ? "desc" : "asc");
    }
  }

  const sortedTrades = useMemo(() => {
    if (!result) return [];
    return [...result.trades].sort((a, b) => {
      const mul = sortDir === "asc" ? 1 : -1;
      if (sortKey === "entry_date")
        return mul * a.entry_date.localeCompare(b.entry_date);
      if (sortKey === "ticker")
        return mul * a.ticker.localeCompare(b.ticker);
      return mul * (a[sortKey] - b[sortKey]);
    });
  }, [result, sortKey, sortDir]);

  function handleTickerToggle(ticker: string) {
    setAllSelected(false);
    setSelectedTickers((prev) =>
      prev.includes(ticker)
        ? prev.filter((t) => t !== ticker)
        : [...prev, ticker]
    );
  }

  function handleExport() {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest-${result.strategy}-${result.start_date}-${result.end_date}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const metrics = result?.metrics;

  const metricCards: { label: string; key: string }[] = [
    { label: "Total P&L", key: "total_pnl" },
    { label: "Sharpe Ratio", key: "sharpe_ratio" },
    { label: "Win Rate", key: "win_rate" },
    { label: "Max Drawdown", key: "max_drawdown" },
    { label: "Profit Factor", key: "profit_factor" },
    { label: "Num Trades", key: "num_trades" },
  ];

  function metricColor(label: string, value: number | string | undefined): string {
    if (value === undefined) return "text-white";
    if (typeof value === "string") return "text-white";
    if (label === "Total P&L" || label === "Avg P&L") return pnlColor(value);
    if (label === "Win Rate") return value >= 0.5 ? "text-green-400" : "text-red-400";
    if (label === "Sharpe Ratio") return value >= 1 ? "text-green-400" : value >= 0 ? "text-yellow-400" : "text-red-400";
    return "text-white";
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Backtest</h1>

      {/* Config Form */}
      <div className="bg-gray-900 rounded-lg p-6 mb-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          {/* Strategy */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              Strategy
            </label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            >
              <option value="combined">Combined</option>
              <option value="short">Short</option>
              <option value="options">Options</option>
            </select>
          </div>

          {/* Start Date */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              Start Date
            </label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            />
          </div>

          {/* End Date */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              End Date
            </label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            />
          </div>

          {/* Hold Days */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              Hold Days
            </label>
            <input
              type="number"
              min={1}
              value={holdDays}
              onChange={(e) => setHoldDays(Number(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            />
          </div>

          {/* Score Threshold */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              Score Threshold
            </label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={scoreThreshold}
              onChange={(e) => setScoreThreshold(Number(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            />
          </div>

          {/* Max Concurrent */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              Max Concurrent
            </label>
            <input
              type="number"
              min={1}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(Number(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            />
          </div>

          {/* Max Position */}
          <div>
            <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
              Max Position ($)
            </label>
            <input
              type="number"
              min={100}
              step={100}
              value={maxPosition}
              onChange={(e) => setMaxPosition(Number(e.target.value))}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
            />
          </div>
        </div>

        {/* Ticker multi-select */}
        <div className="mb-4">
          <label className="block text-gray-400 text-xs uppercase tracking-wider mb-2">
            Tickers
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => {
                setAllSelected(true);
                setSelectedTickers([]);
              }}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                allSelected
                  ? "bg-blue-600 border-blue-500 text-white"
                  : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
              }`}
            >
              All
            </button>
            {watchlistTickers.map((ticker) => (
              <button
                key={ticker}
                onClick={() => handleTickerToggle(ticker)}
                className={`text-xs px-3 py-1 rounded border font-mono transition-colors ${
                  !allSelected && selectedTickers.includes(ticker)
                    ? "bg-blue-600 border-blue-500 text-white"
                    : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
                }`}
              >
                {ticker}
              </button>
            ))}
          </div>
        </div>

        {/* Run button */}
        <button
          onClick={handleRun}
          disabled={loading || (!allSelected && selectedTickers.length === 0)}
          className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 text-white px-6 py-2 rounded text-sm font-medium transition-colors"
        >
          {loading ? "Running..." : "Run Backtest"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 mb-6 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Metric Cards */}
          {metrics && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
              {metricCards.map(({ label, key }) => {
                const value = metrics[key as keyof typeof metrics];
                return (
                  <div key={key} className="bg-gray-900 rounded-lg p-4">
                    <div className="text-gray-500 text-xs uppercase tracking-wider">
                      {label}
                    </div>
                    <div
                      className={`text-xl font-bold font-mono mt-1 ${metricColor(label, value)}`}
                    >
                      {formatMetric(label, value)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Equity Curve */}
          {result.daily_equity && result.daily_equity.length > 0 && (
            <div className="bg-gray-900 rounded-lg p-4 mb-6">
              <h2 className="text-lg font-semibold mb-3">Equity Curve</h2>
              <EquityCurveChart data={result.daily_equity} />
            </div>
          )}

          {/* Trade Table */}
          <div className="bg-gray-900 rounded-lg p-4 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">
                Trades ({result.trades.length})
              </h2>
              <button
                onClick={handleExport}
                className="bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-1.5 rounded text-xs font-medium transition-colors border border-gray-700"
              >
                Export JSON
              </button>
            </div>
            {sortedTrades.length === 0 ? (
              <div className="text-gray-500 text-center py-10">
                No trades generated for this configuration.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-gray-400 border-b border-gray-800 text-left">
                      <SortHeader
                        label="Date"
                        sortKey="entry_date"
                        current={sortKey}
                        dir={sortDir}
                        onClick={handleSort}
                      />
                      <SortHeader
                        label="Ticker"
                        sortKey="ticker"
                        current={sortKey}
                        dir={sortDir}
                        onClick={handleSort}
                      />
                      <th className="py-3 px-3">Exit Date</th>
                      <SortHeader
                        label="P&L"
                        sortKey="pnl"
                        current={sortKey}
                        dir={sortDir}
                        onClick={handleSort}
                        align="right"
                      />
                      <SortHeader
                        label="Return %"
                        sortKey="return_pct"
                        current={sortKey}
                        dir={sortDir}
                        onClick={handleSort}
                        align="right"
                      />
                      <th className="py-3 px-3 text-right">Entry</th>
                      <th className="py-3 px-3 text-right">Exit</th>
                      <th className="py-3 px-3 text-center">Stop/Target</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedTrades.map((trade, i) => (
                      <tr
                        key={`${trade.ticker}-${trade.entry_date}-${i}`}
                        className={`border-b border-gray-800/50 ${
                          trade.pnl > 0
                            ? "hover:bg-green-900/10"
                            : trade.pnl < 0
                              ? "hover:bg-red-900/10"
                              : "hover:bg-gray-900/50"
                        }`}
                      >
                        <td className="py-2 px-3 text-gray-400 text-xs">
                          {trade.entry_date}
                        </td>
                        <td className="py-2 px-3 font-mono font-bold">
                          {trade.ticker}
                        </td>
                        <td className="py-2 px-3 text-gray-400 text-xs">
                          {trade.exit_date}
                        </td>
                        <td
                          className={`py-2 px-3 text-right font-mono font-bold ${pnlColor(trade.pnl)}`}
                        >
                          {dollar(trade.pnl)}
                        </td>
                        <td
                          className={`py-2 px-3 text-right font-mono ${pnlColor(trade.return_pct)}`}
                        >
                          {pct(trade.return_pct)}
                        </td>
                        <td className="py-2 px-3 text-right font-mono text-gray-400">
                          ${trade.entry_price.toFixed(2)}
                        </td>
                        <td className="py-2 px-3 text-right font-mono text-gray-400">
                          ${trade.exit_price.toFixed(2)}
                        </td>
                        <td className="py-2 px-3 text-center">
                          {trade.hit_stop && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-red-900/50 text-red-300 border border-red-800">
                              Stop
                            </span>
                          )}
                          {trade.hit_target && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-green-900/50 text-green-300 border border-green-800">
                              Target
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  current,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: SortDir;
  onClick: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = current === sortKey;
  return (
    <th
      className={`py-3 px-3 cursor-pointer hover:text-gray-200 select-none ${
        align === "right" ? "text-right" : "text-left"
      }`}
      onClick={() => onClick(sortKey)}
    >
      {label}
      {active && (
        <span className="ml-1 text-blue-400">
          {dir === "asc" ? "\u25B2" : "\u25BC"}
        </span>
      )}
    </th>
  );
}
