"use client";

import type { Recommendation } from "@/lib/types";

interface PositionDetailProps {
  recommendation: Recommendation;
}

export default function PositionDetail({ recommendation }: PositionDetailProps) {
  const r = recommendation;
  const isOptions = r.strategy === "options";
  const isSpread = r.strategy === "spread";
  const strategyColor =
    isSpread ? "text-blue-400" : isOptions ? "text-purple-400" : "text-red-400";

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Position Details —{" "}
          <span className={strategyColor}>{r.strategy}</span>
        </h3>
        <span
          className={`text-xs px-2 py-0.5 rounded border ${
            r.risk_type === "defined"
              ? "bg-green-900/50 text-green-300 border-green-800"
              : "bg-yellow-900/50 text-yellow-300 border-yellow-800"
          }`}
        >
          {r.risk_type === "defined" ? "Defined" : "Undefined"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Entry Price" value={dollar(r.entry_price)} />
        <Field label="Stop Loss" value={dollar(r.stop_loss)} className="text-red-400" />
        <Field label="Target Price" value={dollar(r.target_price)} className="text-green-400" />
        <Field label="Position Size" value={dollar(r.position_size)} />
        <Field
          label="Max Loss"
          value={r.max_loss !== null ? dollar(r.max_loss) : "Unlimited"}
          className="text-red-400 font-bold"
        />
        {(isOptions || isSpread) && (
          <>
            <Field label="Contracts" value={String(r.contracts ?? "—")} />
            <Field label="Strike" value={dollar(r.strike)} />
            {isOptions && (
              <Field label="Type" value={r.option_type?.toUpperCase() ?? "—"} />
            )}
          </>
        )}
        {r.notes && (
          <div className="col-span-2">
            <div className="text-gray-500 text-xs">Notes</div>
            <div className="font-mono text-gray-300 text-xs">{r.notes}</div>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div>
      <div className="text-gray-500 text-xs">{label}</div>
      <div className={`font-mono ${className}`}>{value}</div>
    </div>
  );
}

function dollar(val: number | null): string {
  if (val === null) return "—";
  return `$${val.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
