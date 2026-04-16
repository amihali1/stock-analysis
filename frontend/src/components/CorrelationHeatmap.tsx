"use client";

function correlationColor(val: number): string {
  // Red for high positive, blue for high negative, gray for neutral
  if (val >= 0.7) return "bg-red-600";
  if (val >= 0.5) return "bg-red-800";
  if (val >= 0.3) return "bg-red-900/60";
  if (val >= -0.3) return "bg-gray-800";
  if (val >= -0.5) return "bg-blue-900/60";
  if (val >= -0.7) return "bg-blue-800";
  return "bg-blue-600";
}

function correlationText(val: number): string {
  if (Math.abs(val) >= 0.5) return "text-white font-bold";
  return "text-gray-400";
}

export default function CorrelationHeatmap({
  tickers,
  matrix,
}: {
  tickers: string[];
  matrix: number[][];
}) {
  if (tickers.length < 2) {
    return (
      <div className="text-gray-500 text-center py-10 text-sm">
        Need at least 2 open positions for correlation analysis
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-xs">
        <thead>
          <tr>
            <th className="py-1 px-2" />
            {tickers.map((t) => (
              <th key={t} className="py-1 px-2 text-gray-400 font-mono font-normal">
                {t}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tickers.map((rowTicker, i) => (
            <tr key={rowTicker}>
              <td className="py-1 px-2 text-gray-400 font-mono">{rowTicker}</td>
              {tickers.map((_, j) => {
                const val = matrix[i]?.[j] ?? 0;
                return (
                  <td
                    key={j}
                    className={`py-1 px-2 text-center rounded ${correlationColor(val)} ${correlationText(val)}`}
                    title={`${rowTicker} / ${tickers[j]}: ${val.toFixed(2)}`}
                  >
                    {i === j ? "—" : val.toFixed(2)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
