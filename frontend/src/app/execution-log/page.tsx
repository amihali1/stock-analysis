"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getExecutionLog } from "@/lib/api";
import type { ExecutionLogEntry } from "@/lib/types";

type Filter = "all" | "passed" | "blocked";

function actionBadge(action: string) {
  const map: Record<string, string> = {
    submit: "bg-green-900 text-green-300",
    block: "bg-red-900 text-red-300",
    error: "bg-red-900 text-red-300",
    skip: "bg-gray-700 text-gray-300",
    close: "bg-blue-900 text-blue-300",
    emergency_close: "bg-red-800 text-red-200",
    cancel: "bg-gray-700 text-gray-300",
    fill: "bg-green-900 text-green-300",
  };
  const cls = map[action] || "bg-gray-700 text-gray-300";
  return <span className={`text-xs font-mono px-2 py-0.5 rounded ${cls}`}>{action}</span>;
}

export default function ExecutionLogPage() {
  const [entries, setEntries] = useState<ExecutionLogEntry[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [days, setDays] = useState<number>(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getExecutionLog(200);
      setEntries(data);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const filtered = useMemo(() => {
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    return entries.filter((e) => {
      if (filter === "passed" && !e.passed_safety) return false;
      if (filter === "blocked" && e.passed_safety) return false;
      if (e.created_at) {
        const ts = new Date(e.created_at).getTime();
        if (ts < cutoff) return false;
      }
      return true;
    });
  }, [entries, filter, days]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Execution Log</h1>
        <div className="flex gap-2">
          {(["all", "passed", "blocked"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-sm px-3 py-1.5 rounded border ${
                filter === f
                  ? "bg-blue-600 text-white border-blue-500"
                  : "bg-gray-800 text-gray-400 border-gray-700 hover:text-white"
              }`}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
          <select
            value={days}
            onChange={(e) => setDays(parseInt(e.target.value))}
            className="bg-gray-800 border border-gray-700 text-sm px-2 py-1.5 rounded"
          >
            <option value={1}>Last 1 day</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={365}>All</option>
          </select>
        </div>
      </div>

      {loading && entries.length === 0 && (
        <div className="text-gray-500 text-center py-20">Loading…</div>
      )}

      {error && (
        <div className="text-red-400 bg-red-900/20 border border-red-800 rounded p-4 mb-4">
          {error}
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div className="text-gray-500 text-center py-12 text-sm">
          No entries match the filter.
        </div>
      )}

      {filtered.length > 0 && (
        <div className="overflow-x-auto bg-gray-900 rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs uppercase border-b border-gray-800">
                <th className="text-left py-2 px-3">When</th>
                <th className="text-left py-2 px-3">Ticker</th>
                <th className="text-left py-2 px-3">Action</th>
                <th className="text-left py-2 px-3">Strategy</th>
                <th className="text-right py-2 px-3">Qty</th>
                <th className="text-left py-2 px-3">Side</th>
                <th className="text-left py-2 px-3">Order ID</th>
                <th className="text-left py-2 px-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <tr
                  key={e.id}
                  className={`border-b border-gray-800/50 ${
                    !e.passed_safety ? "bg-red-900/10" : ""
                  }`}
                >
                  <td className="py-2 px-3 text-gray-500 text-xs whitespace-nowrap">
                    {e.created_at ? new Date(e.created_at).toLocaleString() : "-"}
                  </td>
                  <td className="py-2 px-3 font-mono font-bold">{e.ticker}</td>
                  <td className="py-2 px-3">{actionBadge(e.action)}</td>
                  <td className="py-2 px-3">{e.strategy || "-"}</td>
                  <td className="py-2 px-3 text-right font-mono">{e.qty ?? "-"}</td>
                  <td className="py-2 px-3">{e.side || "-"}</td>
                  <td className="py-2 px-3 font-mono text-xs text-gray-500">
                    {e.order_id ? e.order_id.slice(0, 12) + "…" : "-"}
                  </td>
                  <td className="py-2 px-3 text-gray-400 text-xs max-w-md truncate">
                    {e.reason || "-"}
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
