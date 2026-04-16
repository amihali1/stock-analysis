"use client";

const SECTOR_COLORS: Record<string, string> = {
  Technology: "#3b82f6",
  "Financial Services": "#10b981",
  Healthcare: "#ec4899",
  "Consumer Cyclical": "#f59e0b",
  "Consumer Defensive": "#f97316",
  Energy: "#ef4444",
  Industrials: "#06b6d4",
  "Communication Services": "#8b5cf6",
  Unknown: "#6b7280",
};

function getColor(sector: string, index: number): string {
  return SECTOR_COLORS[sector] || `hsl(${(index * 47) % 360}, 60%, 55%)`;
}

interface SectorData {
  amount: number;
  percentage: number;
  over_limit: boolean;
}

export default function SectorAllocation({
  sectors,
  totalExposure,
  maxSectorPct,
}: {
  sectors: Record<string, SectorData>;
  totalExposure: number;
  maxSectorPct: number;
}) {
  const entries = Object.entries(sectors);

  if (entries.length === 0) {
    return (
      <div className="text-gray-500 text-center py-10 text-sm">
        No open positions
      </div>
    );
  }

  return (
    <div>
      {/* Bar chart */}
      <div className="space-y-2 mb-4">
        {entries.map(([sector, data], i) => (
          <div key={sector}>
            <div className="flex justify-between text-xs mb-0.5">
              <span className="text-gray-300">{sector}</span>
              <span className={data.over_limit ? "text-red-400 font-bold" : "text-gray-400"}>
                {(data.percentage * 100).toFixed(1)}% — $
                {data.amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </span>
            </div>
            <div className="h-3 bg-gray-800 rounded-full overflow-hidden relative">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${Math.min(data.percentage * 100, 100)}%`,
                  backgroundColor: getColor(sector, i),
                }}
              />
              {/* Limit marker */}
              <div
                className="absolute top-0 h-full w-px bg-yellow-500/60"
                style={{ left: `${maxSectorPct * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="text-xs text-gray-500">
        Total exposure: $
        {totalExposure.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        {" | "}Sector limit: {(maxSectorPct * 100).toFixed(0)}%
      </div>
    </div>
  );
}
