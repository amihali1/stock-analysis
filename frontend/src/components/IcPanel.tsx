"use client";

import { useEffect, useState } from "react";

import { getInformationCoefficient } from "@/lib/api";
import type { IcSignal } from "@/lib/types";

/**
 * Information Coefficient panel — rank-correlation of each signal vs forward
 * return. This is the ranker's go/no-go metric (the 2026-09-08 audit found the
 * composite score's IC was negative). Positive IC means the signal ranks in its
 * intended direction; IR is the stability-adjusted version.
 */
function icColor(ic: number): string {
  if (ic >= 0.03) return "text-green-400";
  if (ic <= -0.03) return "text-red-400";
  return "text-gray-400";
}

export default function IcPanel() {
  const [signals, setSignals] = useState<IcSignal[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getInformationCoefficient(60, "universe")
      .then((r) => setSignals(r.signals))
      .catch((e) => setError(e.message || "failed to load IC"));
  }, []);

  return (
    <div className="bg-gray-900 rounded-lg p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Information Coefficient
        </h3>
        <span className="text-xs text-gray-500">60d · universe</span>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      {!error && signals === null && (
        <p className="text-sm text-gray-500">Loading…</p>
      )}
      {!error && signals !== null && signals.length === 0 && (
        <p className="text-sm text-gray-500">
          No IC data yet — accrues after the nightly job runs on the full universe.
        </p>
      )}

      {signals && signals.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-500 text-left border-b border-gray-800">
              <th className="py-1 font-normal">Signal</th>
              <th className="py-1 font-normal text-right">Horizon</th>
              <th className="py-1 font-normal text-right">Mean IC</th>
              <th className="py-1 font-normal text-right">IR</th>
              <th className="py-1 font-normal text-right">Days</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {signals.map((s) => (
              <tr key={`${s.signal}-${s.horizon}`} className="border-b border-gray-800/50">
                <td className="py-1 font-sans text-gray-300">{s.signal}</td>
                <td className="py-1 text-right text-gray-400">{s.horizon}d</td>
                <td className={`py-1 text-right ${icColor(s.mean_ic)}`}>
                  {s.mean_ic >= 0 ? "+" : ""}
                  {s.mean_ic.toFixed(3)}
                </td>
                <td className="py-1 text-right text-gray-300">
                  {s.ir >= 0 ? "+" : ""}
                  {s.ir.toFixed(2)}
                </td>
                <td className="py-1 text-right text-gray-500">{s.days}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
