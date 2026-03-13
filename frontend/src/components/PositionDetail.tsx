"use client";

import type { Recommendation } from "@/lib/types";

interface PositionDetailProps {
  recommendation: Recommendation;
}

export default function PositionDetail({ recommendation }: PositionDetailProps) {
  const r = recommendation;
  const isOptions = r.strategy === "options";

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
        Position Details —{" "}
        <span className={isOptions ? "text-purple-400" : "text-red-400"}>
          {r.strategy}
        </span>
      </h3>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Entry Price" value={dollar(r.entry_price)} />
        <Field label="Stop Loss" value={dollar(r.stop_loss)} className="text-red-400" />
        <Field label="Target Price" value={dollar(r.target_price)} className="text-green-400" />
        <Field label="Position Size" value={dollar(r.position_size)} />
        <Field
          label="Max Loss"
          value={dollar(r.max_loss)}
          className="text-red-400 font-bold"
        />
        {isOptions && (
          <>
            <Field label="Contracts" value={String(r.contracts ?? "—")} />
            <Field label="Strike" value={dollar(r.strike)} />
            <Field label="Type" value={r.option_type?.toUpperCase() ?? "—"} />
          </>
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
