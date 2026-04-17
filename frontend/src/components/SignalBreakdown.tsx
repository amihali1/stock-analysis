"use client";

import type { Recommendation } from "@/lib/types";

interface SignalBreakdownProps {
  recommendation: Recommendation;
}

function SignalBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number | null;
  color: string;
}) {
  const v = value ?? 0;
  const pct = Math.round(v * 100);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-gray-400">{label}</span>
        <span className="font-mono">{pct}%</span>
      </div>
      <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

export default function SignalBreakdown({ recommendation }: SignalBreakdownProps) {
  const r = recommendation;
  return (
    <div className="bg-gray-900 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
        Signal Breakdown
      </h3>
      <SignalBar
        label="Directional (bearish)"
        value={r.directional_signal}
        color="bg-red-500"
      />
      <SignalBar
        label="Volatility"
        value={r.volatility_signal}
        color="bg-amber-500"
      />
      <SignalBar
        label="Sentiment (bearish)"
        value={r.sentiment_signal}
        color="bg-purple-500"
      />
      <div className="pt-2 border-t border-gray-800 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-gray-400">Ensemble Score</span>
          <span className="font-mono font-bold text-lg">
            {r.score.toFixed(2)}
          </span>
        </div>
        <div className="flex justify-between text-sm items-center">
          <span className="text-gray-400">Risk Type</span>
          <span
            className={`text-xs px-2 py-0.5 rounded border ${
              r.risk_type === "defined"
                ? "bg-green-900/50 text-green-300 border-green-800"
                : "bg-yellow-900/50 text-yellow-300 border-yellow-800"
            }`}
          >
            {r.risk_type === "defined" ? "Defined Risk" : "Undefined Risk"}
          </span>
        </div>
      </div>
    </div>
  );
}
