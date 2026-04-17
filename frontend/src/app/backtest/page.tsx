"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { runBacktest, compareStrategies, getWatchlist } from "@/lib/api";
import {
  createChart,
  type IChartApi,
  type LineData,
  type Time,
  ColorType,
} from "lightweight-charts";
import EquityCurveChart from "@/components/EquityCurveChart";
import type {
  BacktestConfig,
  BacktestResponse,
  BacktestCompareResponse,
  BacktestTrade,
  WatchlistItem,
  DailyEquityPoint,
} from "@/lib/types";

type SortKey = "entry_date" | "ticker" | "pnl" | "return_pct";
type SortDir = "asc" | "desc";
type Tab = "single" | "compare";

const STRATEGIES = ["short", "options", "combined"] as const;
const STRATEGY_COLORS: Record<string, string> = {
  short: "#ef4444",
  options: "#a855f7",
  combined: "#3b82f6",
};

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

function metricColor(label: string, value: number | string | undefined): string {
  if (value === undefined) return "text-white";
  if (typeof value === "string") return "text-white";
  if (label === "Total P&L" || label === "Avg P&L") return pnlColor(value);
  if (label === "Win Rate") return value >= 0.5 ? "text-green-400" : "text-red-400";
  if (label === "Sharpe Ratio") return value >= 1 ? "text-green-400" : value >= 0 ? "text-yellow-400" : "text-red-400";
  return "text-white";
}

const METRIC_ROWS: { label: string; key: string; higher_is_better: boolean }[] = [
  { label: "Total P&L", key: "total_pnl", higher_is_better: true },
  { label: "Num Trades", key: "num_trades", higher_is_better: true },
  { label: "Win Rate", key: "win_rate", higher_is_better: true },
  { label: "Sharpe Ratio", key: "sharpe_ratio", higher_is_better: true },
  { label: "Max Drawdown", key: "max_drawdown", higher_is_better: false },
  { label: "Profit Factor", key: "profit_factor", higher_is_better: true },
];

export default function BacktestPage() {
  const [tab, setTab] = useState<Tab>("single");

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
  const [maxPosition, setMaxPosition] = useState(1000);

  // Single strategy results
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Compare results
  const [compareResult, setCompareResult] = useState<BacktestCompareResponse | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null);

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

  async function handleCompare() {
    setCompareLoading(true);
    setCompareError(null);
    setCompareResult(null);
    setExpandedStrategy(null);
    try {
      const res = await compareStrategies({
        tickers: allSelected ? null : selectedTickers,
        start_date: startDate || null,
        end_date: endDate || null,
        max_position: maxPosition,
        hold_days: holdDays,
        score_threshold: scoreThreshold,
        max_concurrent: maxConcurrent,
      });
      setCompareResult(res);
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : "Comparison failed");
    } finally {
      setCompareLoading(false);
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
  const isLoading = tab === "single" ? loading : compareLoading;

  const metricCards: { label: string; key: string }[] = [
    { label: "Total P&L", key: "total_pnl" },
    { label: "Sharpe Ratio", key: "sharpe_ratio" },
    { label: "Win Rate", key: "win_rate" },
    { label: "Max Drawdown", key: "max_drawdown" },
    { label: "Profit Factor", key: "profit_factor" },
    { label: "Num Trades", key: "num_trades" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Backtest</h1>

      {/* Tab Toggle */}
      <div className="flex gap-1 mb-6 bg-gray-900 rounded-lg p-1 w-fit">
        <button
          onClick={() => setTab("single")}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            tab === "single"
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white"
          }`}
        >
          Single Strategy
        </button>
        <button
          onClick={() => setTab("compare")}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            tab === "compare"
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white"
          }`}
        >
          Compare Strategies
        </button>
      </div>

      {/* Config Form */}
      <div className="bg-gray-900 rounded-lg p-6 mb-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          {/* Strategy - only shown in single tab */}
          {tab === "single" && (
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
          )}

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
          onClick={tab === "single" ? handleRun : handleCompare}
          disabled={isLoading || (!allSelected && selectedTickers.length === 0)}
          className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 text-white px-6 py-2 rounded text-sm font-medium transition-colors"
        >
          {isLoading
            ? "Running..."
            : tab === "single"
              ? "Run Backtest"
              : "Compare All Strategies"}
        </button>
      </div>

      {/* Error */}
      {(tab === "single" ? error : compareError) && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 mb-6 text-red-300 text-sm">
          {tab === "single" ? error : compareError}
        </div>
      )}

      {/* Single Strategy Results */}
      {tab === "single" && result && (
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
                      <SortHeader label="Date" sortKey="entry_date" current={sortKey} dir={sortDir} onClick={handleSort} />
                      <SortHeader label="Ticker" sortKey="ticker" current={sortKey} dir={sortDir} onClick={handleSort} />
                      <th className="py-3 px-3">Exit Date</th>
                      <SortHeader label="P&L" sortKey="pnl" current={sortKey} dir={sortDir} onClick={handleSort} align="right" />
                      <SortHeader label="Return %" sortKey="return_pct" current={sortKey} dir={sortDir} onClick={handleSort} align="right" />
                      <th className="py-3 px-3 text-right">Entry</th>
                      <th className="py-3 px-3 text-right">Exit</th>
                      <th className="py-3 px-3 text-center">Stop/Target</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedTrades.map((trade, i) => (
                      <TradeRow key={`${trade.ticker}-${trade.entry_date}-${i}`} trade={trade} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {/* Compare Results */}
      {tab === "compare" && compareResult && (
        <>
          {/* Side-by-side Metrics Table */}
          <div className="bg-gray-900 rounded-lg p-4 mb-6 overflow-x-auto">
            <h2 className="text-lg font-semibold mb-4">Strategy Comparison</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="py-3 px-3 text-left">Metric</th>
                  {STRATEGIES.map((s) => (
                    <th key={s} className="py-3 px-3 text-right capitalize">
                      {s}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {METRIC_ROWS.map(({ label, key, higher_is_better }) => {
                  const values = STRATEGIES.map((s) => {
                    const v = compareResult.summary[s]?.[key as keyof typeof compareResult.summary.short];
                    return typeof v === "string" && v === "inf" ? Infinity : (v as number);
                  });
                  const best = higher_is_better
                    ? Math.max(...values.filter((v) => isFinite(v)))
                    : Math.min(...values.filter((v) => isFinite(v)));

                  return (
                    <tr key={key} className="border-b border-gray-800/50">
                      <td className="py-3 px-3 text-gray-400">{label}</td>
                      {STRATEGIES.map((s, i) => {
                        const raw = compareResult.summary[s]?.[key as keyof typeof compareResult.summary.short];
                        const isBest = values[i] === best;
                        return (
                          <td
                            key={s}
                            className={`py-3 px-3 text-right font-mono ${
                              isBest ? "text-green-400 font-bold" : "text-gray-300"
                            }`}
                          >
                            {formatMetric(label, raw)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Overlaid Equity Curves */}
          <CompareEquityChart strategies={compareResult.strategies} />

          {/* Collapsible Per-Strategy Trade Tables */}
          {STRATEGIES.map((s) => {
            const stratResult = compareResult.strategies[s];
            if (!stratResult) return null;
            const isExpanded = expandedStrategy === s;
            return (
              <div key={s} className="bg-gray-900 rounded-lg mb-4">
                <button
                  onClick={() => setExpandedStrategy(isExpanded ? null : s)}
                  className="w-full flex items-center justify-between p-4 text-left hover:bg-gray-800/50 rounded-lg transition-colors"
                >
                  <span className="font-semibold capitalize">
                    {s} Trades ({stratResult.trades.length})
                  </span>
                  <span className="text-gray-500 text-sm">
                    {isExpanded ? "\u25B2" : "\u25BC"}
                  </span>
                </button>
                {isExpanded && (
                  <div className="px-4 pb-4 overflow-x-auto">
                    {stratResult.trades.length === 0 ? (
                      <div className="text-gray-500 text-center py-6">
                        No trades for this strategy.
                      </div>
                    ) : (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-gray-400 border-b border-gray-800 text-left">
                            <th className="py-3 px-3">Date</th>
                            <th className="py-3 px-3">Ticker</th>
                            <th className="py-3 px-3">Exit Date</th>
                            <th className="py-3 px-3 text-right">P&L</th>
                            <th className="py-3 px-3 text-right">Return %</th>
                            <th className="py-3 px-3 text-right">Entry</th>
                            <th className="py-3 px-3 text-right">Exit</th>
                            <th className="py-3 px-3 text-center">Stop/Target</th>
                          </tr>
                        </thead>
                        <tbody>
                          {stratResult.trades.map((trade, i) => (
                            <TradeRow key={`${trade.ticker}-${trade.entry_date}-${i}`} trade={trade} />
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

function TradeRow({ trade }: { trade: BacktestTrade }) {
  return (
    <tr
      className={`border-b border-gray-800/50 ${
        trade.pnl > 0
          ? "hover:bg-green-900/10"
          : trade.pnl < 0
            ? "hover:bg-red-900/10"
            : "hover:bg-gray-900/50"
      }`}
    >
      <td className="py-2 px-3 text-gray-400 text-xs">{trade.entry_date}</td>
      <td className="py-2 px-3 font-mono font-bold">{trade.ticker}</td>
      <td className="py-2 px-3 text-gray-400 text-xs">{trade.exit_date}</td>
      <td className={`py-2 px-3 text-right font-mono font-bold ${pnlColor(trade.pnl)}`}>
        {dollar(trade.pnl)}
      </td>
      <td className={`py-2 px-3 text-right font-mono ${pnlColor(trade.return_pct)}`}>
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
  );
}

function CompareEquityChart({
  strategies,
}: {
  strategies: Record<string, BacktestResponse>;
}) {
  const hasData = STRATEGIES.some(
    (s) => strategies[s]?.daily_equity && strategies[s].daily_equity!.length > 0
  );
  if (!hasData) return null;

  // Merge all equity data into a single dataset keyed by date for the chart
  // We'll use EquityCurveChart-like rendering but with 3 series
  return (
    <div className="bg-gray-900 rounded-lg p-4 mb-6">
      <h2 className="text-lg font-semibold mb-3">Equity Curves</h2>
      <div className="flex gap-4 mb-2">
        {STRATEGIES.map((s) => (
          <div key={s} className="flex items-center gap-1.5 text-xs text-gray-400">
            <div
              className="w-3 h-0.5 rounded"
              style={{ backgroundColor: STRATEGY_COLORS[s] }}
            />
            <span className="capitalize">{s}</span>
          </div>
        ))}
      </div>
      <MultiEquityChart strategies={strategies} />
    </div>
  );
}

function MultiEquityChart({
  strategies,
}: {
  strategies: Record<string, BacktestResponse>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0a0a0a" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      width: containerRef.current.clientWidth,
      height: 300,
      crosshair: { mode: 0 },
      timeScale: { borderColor: "#374151" },
    });

    for (const s of STRATEGIES) {
      const equity = strategies[s]?.daily_equity;
      if (!equity || equity.length === 0) continue;
      const series = chart.addLineSeries({
        color: STRATEGY_COLORS[s],
        lineWidth: 2,
        title: s,
      });
      const lineData: LineData[] = equity.map((d) => ({
        time: d.date as Time,
        value: d.total_equity,
      }));
      series.setData(lineData);
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [strategies]);

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden" />;
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
