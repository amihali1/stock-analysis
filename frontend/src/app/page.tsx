"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { getRecommendations } from "@/lib/api";
import type { Recommendation, Strategy } from "@/lib/types";

function scoreColor(score: number): string {
  if (score >= 0.7) return "text-green-400";
  if (score >= 0.5) return "text-yellow-400";
  return "text-gray-400";
}

function strategyBadge(strategy: Strategy) {
  const cls =
    strategy === "short"
      ? "bg-red-900/50 text-red-300 border-red-800"
      : strategy === "spread"
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

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getRecommendations(strategyFilter || undefined, 50);
      setRecs(data.recommendations);
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

  const sorted = [...recs].sort((a, b) => {
    let cmp = 0;
    if (sortKey === "ticker") cmp = a.ticker.localeCompare(b.ticker);
    else cmp = ((a[sortKey] ?? 0) as number) - ((b[sortKey] ?? 0) as number);
    return sortAsc ? cmp : -cmp;
  });

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortAsc ? " \u25B2" : " \u25BC") : "";

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
              </tr>
            </thead>
            <tbody>
              {sorted.map((rec, i) => (
                <Link
                  key={`${rec.ticker}-${rec.strategy}-${i}`}
                  href={`/analysis/${rec.ticker}`}
                  className="contents"
                >
                  <tr className="border-b border-gray-800/50 hover:bg-gray-900/50 cursor-pointer transition-colors">
                    <td className="py-3 px-3 font-mono font-bold">
                      {rec.ticker}
                    </td>
                    <td className="py-3 px-3">
                      {strategyBadge(rec.strategy)}
                    </td>
                    <td className="py-3 px-3">
                      {riskBadge(rec.risk_type)}
                    </td>
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
                  </tr>
                </Link>
              ))}
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
