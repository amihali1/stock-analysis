"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getSafetyStatus,
  getTradingSettings,
  updateTradingSettings,
} from "@/lib/api";
import type { SafetyStatus, TradingMode, TradingSettings } from "@/lib/types";

const MODE_COPY: Record<TradingMode, { label: string; color: string }> = {
  disabled: { label: "Disabled", color: "bg-gray-700 border-gray-600 text-gray-200" },
  paper: { label: "Paper", color: "bg-yellow-900 border-yellow-700 text-yellow-200" },
  live: { label: "Live", color: "bg-red-800 border-red-600 text-red-100" },
};

export default function TradingControls() {
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  const [safety, setSafety] = useState<SafetyStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingMode, setPendingMode] = useState<TradingMode | null>(null);
  const [confirmText, setConfirmText] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, st] = await Promise.all([getTradingSettings(), getSafetyStatus()]);
      setSettings(s);
      setSafety(st);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const writeSetting = async (
    updates: Partial<TradingSettings> & { confirm?: string }
  ) => {
    setBusy(true);
    try {
      const next = await updateTradingSettings(updates);
      setSettings(next);
      setError(null);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  };

  const requestModeChange = (mode: TradingMode) => {
    if (!settings || mode === settings.trading_mode) return;
    if (mode === "live") {
      setPendingMode("live");
      setConfirmText("");
      return;
    }
    writeSetting({ trading_mode: mode });
  };

  const confirmLive = async () => {
    if (confirmText !== "CONFIRM") return;
    await writeSetting({ trading_mode: "live", confirm: "CONFIRM" });
    setPendingMode(null);
    setConfirmText("");
  };

  if (!settings || !safety) {
    return (
      <div className="text-gray-500 text-sm py-6">Loading trading controls…</div>
    );
  }

  const lossPct = safety.max_daily_loss > 0
    ? Math.min(100, (safety.daily_loss / safety.max_daily_loss) * 100)
    : 0;
  const posPct = safety.max_open_positions > 0
    ? Math.min(100, (safety.open_positions / safety.max_open_positions) * 100)
    : 0;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Trading Controls</h2>
        <span
          className={`text-xs px-2 py-1 rounded border font-mono uppercase ${MODE_COPY[settings.trading_mode].color}`}
        >
          {MODE_COPY[settings.trading_mode].label}
        </span>
      </div>

      {error && (
        <div className="text-red-400 text-sm bg-red-900/20 border border-red-800 rounded p-2">
          {error}
        </div>
      )}

      <div>
        <div className="text-xs uppercase text-gray-500 tracking-wider mb-2">
          Mode
        </div>
        <div className="flex gap-2">
          {(["disabled", "paper", "live"] as TradingMode[]).map((m) => (
            <button
              key={m}
              disabled={busy}
              onClick={() => requestModeChange(m)}
              className={`text-sm px-3 py-1.5 rounded border transition-colors ${
                settings.trading_mode === m
                  ? MODE_COPY[m].color
                  : "bg-gray-800 border-gray-700 text-gray-400 hover:text-white"
              }`}
            >
              {MODE_COPY[m].label}
            </button>
          ))}
        </div>
      </div>

      {pendingMode === "live" && (
        <div className="bg-red-900/20 border border-red-800 rounded p-3 space-y-2">
          <div className="text-red-300 text-sm">
            Switching to <strong>Live</strong> trading. Type <code className="bg-black/50 px-1 rounded">CONFIRM</code> to proceed.
          </div>
          <div className="flex gap-2">
            <input
              autoFocus
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              className="flex-1 bg-black border border-red-800 rounded px-2 py-1 font-mono text-sm"
              placeholder="CONFIRM"
            />
            <button
              onClick={confirmLive}
              disabled={confirmText !== "CONFIRM" || busy}
              className="bg-red-700 disabled:bg-gray-800 disabled:text-gray-500 hover:bg-red-600 text-white text-sm px-3 py-1 rounded"
            >
              Go Live
            </button>
            <button
              onClick={() => {
                setPendingMode(null);
                setConfirmText("");
              }}
              className="bg-gray-800 hover:bg-gray-700 text-sm px-3 py-1 rounded"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div>
        <div className="text-xs uppercase text-gray-500 tracking-wider mb-2">
          Auto-execute Recommendations
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={settings.auto_execute_enabled}
            disabled={busy}
            onChange={(e) =>
              writeSetting({ auto_execute_enabled: e.target.checked })
            }
            className="h-4 w-4"
          />
          <span className="text-sm">
            {settings.auto_execute_enabled ? "Enabled" : "Disabled"}
          </span>
        </label>
      </div>

      <div>
        <div className="flex justify-between mb-2">
          <span className="text-xs uppercase text-gray-500 tracking-wider">
            Auto-execute Score Threshold
          </span>
          <span className="text-sm font-mono text-gray-300">
            {settings.min_score_threshold.toFixed(2)}
          </span>
        </div>
        <input
          type="range"
          min={0.5}
          max={0.95}
          step={0.05}
          value={settings.min_score_threshold}
          disabled={busy}
          onChange={(e) =>
            writeSetting({ min_score_threshold: parseFloat(e.target.value) })
          }
          className="w-full"
        />
      </div>

      <div className="border-t border-gray-800 pt-4 space-y-3">
        <div className="text-xs uppercase text-gray-500 tracking-wider">
          Safety Rails
        </div>

        <RailBar
          label={`Daily Loss — $${safety.daily_loss.toFixed(2)} / $${safety.max_daily_loss.toFixed(0)}`}
          pct={lossPct}
          danger={lossPct >= 80}
        />
        <RailBar
          label={`Open Positions — ${safety.open_positions} / ${safety.max_open_positions}`}
          pct={posPct}
          danger={posPct >= 80}
        />

        <div className="text-xs text-gray-500">
          Max single position ${safety.max_single_position.toFixed(0)} ·{" "}
          {safety.daily_orders} / {safety.max_daily_orders} orders today ·{" "}
          {safety.market_hours_only ? "market hours only" : "any hours"}
        </div>
      </div>
    </div>
  );
}

function RailBar({
  label,
  pct,
  danger,
}: {
  label: string;
  pct: number;
  danger: boolean;
}) {
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
      </div>
      <div className="h-2 bg-gray-800 rounded overflow-hidden">
        <div
          className={`h-full transition-all ${
            danger ? "bg-red-500" : "bg-green-600"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
