"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { getAnalysis } from "@/lib/api";
import type { AnalysisResponse } from "@/lib/types";
import StockChart from "@/components/StockChart";
import SignalBreakdown from "@/components/SignalBreakdown";
import SentimentGauge from "@/components/SentimentGauge";
import PositionDetail from "@/components/PositionDetail";

export default function AnalysisPage() {
  const params = useParams();
  const ticker = (params.ticker as string).toUpperCase();

  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        const analysis = await getAnalysis(ticker, 180);
        setData(analysis);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load analysis");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [ticker]);

  if (loading) {
    return (
      <div className="text-gray-500 text-center py-20">
        Loading analysis for {ticker}...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="text-center py-20">
        <div className="text-red-400 mb-4">{error || "No data found"}</div>
        <Link href="/" className="text-blue-400 hover:underline">
          Back to Dashboard
        </Link>
      </div>
    );
  }

  const latestPrice = data.latest_price;

  return (
    <div>
      {/* Header */}
      <div className="flex items-baseline gap-4 mb-6">
        <Link href="/" className="text-gray-500 hover:text-white text-sm">
          &larr; Dashboard
        </Link>
        <h1 className="text-2xl font-bold font-mono">{data.ticker}</h1>
        {data.name && <span className="text-gray-400">{data.name}</span>}
        {data.sector && (
          <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
            {data.sector}
          </span>
        )}
        {latestPrice?.close != null && (
          <span className="text-xl font-mono">
            ${latestPrice.close.toFixed(2)}
          </span>
        )}
      </div>

      {/* Chart */}
      {data.prices.length > 0 && (
        <div className="mb-6">
          <StockChart
            prices={data.prices}
            indicators={data.indicators}
            height={450}
          />
        </div>
      )}

      {/* Signals + Sentiment + Position grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        {data.recommendations.length > 0 && (
          <SignalBreakdown recommendation={data.recommendations[0]} />
        )}

        <SentimentGauge sentiments={data.sentiments} />

        {data.recommendations.map((rec, i) => (
          <PositionDetail key={i} recommendation={rec} />
        ))}
      </div>

      {/* Indicator summary */}
      {data.indicators.length > 0 && (
        <div className="bg-gray-900 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
            Latest Technical Indicators
          </h3>
          <LatestIndicators indicator={data.indicators[data.indicators.length - 1]} />
        </div>
      )}
    </div>
  );
}

function LatestIndicators({
  indicator,
}: {
  indicator: {
    rsi_14: number | null;
    macd: number | null;
    macd_histogram: number | null;
    sma_50: number | null;
    sma_200: number | null;
    volume_zscore: number | null;
    bb_upper: number | null;
    bb_lower: number | null;
  };
}) {
  const i = indicator;
  const rsiColor =
    (i.rsi_14 ?? 50) > 70
      ? "text-red-400"
      : (i.rsi_14 ?? 50) < 30
        ? "text-green-400"
        : "text-gray-100";

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
      <div>
        <div className="text-gray-500 text-xs">RSI (14)</div>
        <div className={`font-mono ${rsiColor}`}>
          {i.rsi_14?.toFixed(1) ?? "—"}
        </div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">MACD</div>
        <div className="font-mono">{i.macd?.toFixed(3) ?? "—"}</div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">MACD Histogram</div>
        <div
          className={`font-mono ${(i.macd_histogram ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}
        >
          {i.macd_histogram?.toFixed(3) ?? "—"}
        </div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">Volume Z-Score</div>
        <div className="font-mono">
          {i.volume_zscore?.toFixed(2) ?? "—"}
        </div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">SMA 50</div>
        <div className="font-mono">{i.sma_50?.toFixed(2) ?? "—"}</div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">SMA 200</div>
        <div className="font-mono">{i.sma_200?.toFixed(2) ?? "—"}</div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">BB Upper</div>
        <div className="font-mono">{i.bb_upper?.toFixed(2) ?? "—"}</div>
      </div>
      <div>
        <div className="text-gray-500 text-xs">BB Lower</div>
        <div className="font-mono">{i.bb_lower?.toFixed(2) ?? "—"}</div>
      </div>
    </div>
  );
}
