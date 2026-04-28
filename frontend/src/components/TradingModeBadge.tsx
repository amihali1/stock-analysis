"use client";

import { useEffect, useState } from "react";
import { getTradingSettings } from "@/lib/api";
import type { TradingMode } from "@/lib/types";

const STYLE: Record<TradingMode, string> = {
  disabled: "bg-gray-700 text-gray-200 border-gray-600",
  paper: "bg-yellow-900 text-yellow-200 border-yellow-700",
  live: "bg-red-800 text-red-100 border-red-600 animate-pulse",
};

const LABEL: Record<TradingMode, string> = {
  disabled: "OFF",
  paper: "PAPER",
  live: "LIVE",
};

export default function TradingModeBadge() {
  const [mode, setMode] = useState<TradingMode | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getTradingSettings()
        .then((s) => {
          if (!cancelled) setMode(s.trading_mode);
        })
        .catch(() => {
          if (!cancelled) setMode(null);
        });
    load();
    const t = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (!mode) return null;

  return (
    <span
      className={`text-[10px] font-mono px-2 py-0.5 rounded border ${STYLE[mode]}`}
      title={`Trading mode: ${mode}`}
    >
      {LABEL[mode]}
    </span>
  );
}
