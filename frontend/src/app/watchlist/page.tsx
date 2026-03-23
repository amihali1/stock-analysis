"use client";

import { useEffect, useState } from "react";
import { getWatchlist, addToWatchlist, removeFromWatchlist } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";

const SECTOR_COLORS: Record<string, string> = {
  Technology: "bg-blue-900/50 text-blue-300 border-blue-800",
  "Financial Services": "bg-green-900/50 text-green-300 border-green-800",
  Healthcare: "bg-pink-900/50 text-pink-300 border-pink-800",
  "Consumer Cyclical": "bg-yellow-900/50 text-yellow-300 border-yellow-800",
  "Consumer Defensive": "bg-orange-900/50 text-orange-300 border-orange-800",
  Energy: "bg-red-900/50 text-red-300 border-red-800",
  Industrials: "bg-cyan-900/50 text-cyan-300 border-cyan-800",
  "Communication Services": "bg-purple-900/50 text-purple-300 border-purple-800",
};

function sectorBadge(sector: string | null) {
  if (!sector) return null;
  const cls =
    SECTOR_COLORS[sector] || "bg-gray-800 text-gray-300 border-gray-700";
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${cls}`}>
      {sector}
    </span>
  );
}

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tickerInput, setTickerInput] = useState("");
  const [bulkInput, setBulkInput] = useState("");
  const [adding, setAdding] = useState(false);

  async function load() {
    try {
      const data = await getWatchlist();
      setItems(data.tickers);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load watchlist");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleAdd() {
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker) return;
    setAdding(true);
    try {
      const data = await addToWatchlist([ticker]);
      setItems(data.tickers);
      setTickerInput("");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add ticker");
    } finally {
      setAdding(false);
    }
  }

  async function handleBulkImport() {
    const tickers = bulkInput
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter((t) => t.length > 0);
    if (tickers.length === 0) return;
    setAdding(true);
    try {
      const data = await addToWatchlist(tickers);
      setItems(data.tickers);
      setBulkInput("");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to import tickers");
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(ticker: string) {
    try {
      await removeFromWatchlist(ticker);
      setItems((prev) => prev.filter((item) => item.ticker !== ticker));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove ticker");
    }
  }

  if (loading) {
    return (
      <div className="text-gray-500 text-center py-20">
        Loading watchlist...
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Watchlist</h1>

      {error && (
        <div className="text-red-400 bg-red-900/20 border border-red-800 rounded p-4 mb-4">
          {error}
        </div>
      )}

      {/* Add single ticker */}
      <div className="flex gap-2 mb-4">
        <input
          type="text"
          value={tickerInput}
          onChange={(e) => setTickerInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Add ticker (e.g. PLTR)"
          className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-48"
        />
        <button
          onClick={handleAdd}
          disabled={adding || !tickerInput.trim()}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm px-4 py-2 rounded transition-colors"
        >
          Add
        </button>
      </div>

      {/* Bulk import */}
      <div className="mb-6">
        <textarea
          value={bulkInput}
          onChange={(e) => setBulkInput(e.target.value)}
          placeholder="Bulk import: paste comma-separated tickers (e.g. PLTR, SOFI, RIVN)"
          rows={2}
          className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-full"
        />
        <button
          onClick={handleBulkImport}
          disabled={adding || !bulkInput.trim()}
          className="mt-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm px-4 py-2 rounded transition-colors"
        >
          Import
        </button>
      </div>

      {/* Watchlist count */}
      <div className="text-gray-500 text-sm mb-3">
        {items.length} ticker{items.length !== 1 && "s"} in watchlist
      </div>

      {/* Watchlist table */}
      {items.length === 0 ? (
        <div className="text-gray-500 text-center py-20">
          Watchlist is empty. Add tickers above to get started.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-800 text-left">
                <th className="py-3 px-3">Ticker</th>
                <th className="py-3 px-3">Sector</th>
                <th className="py-3 px-3">Added</th>
                <th className="py-3 px-3 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.ticker}
                  className="border-b border-gray-800/50 hover:bg-gray-900/50"
                >
                  <td className="py-3 px-3 font-mono font-bold">
                    {item.ticker}
                  </td>
                  <td className="py-3 px-3">{sectorBadge(item.sector)}</td>
                  <td className="py-3 px-3 text-gray-500 text-xs">
                    {item.added_at
                      ? new Date(item.added_at).toLocaleDateString()
                      : "—"}
                  </td>
                  <td className="py-3 px-3">
                    <button
                      onClick={() => handleRemove(item.ticker)}
                      className="text-red-400 hover:text-red-300 text-xs transition-colors"
                    >
                      Remove
                    </button>
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
