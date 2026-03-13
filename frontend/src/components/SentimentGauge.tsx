"use client";

import type { SentimentEntry } from "@/lib/types";

interface SentimentGaugeProps {
  sentiments: SentimentEntry[];
}

function sentimentColor(val: number): string {
  if (val >= 0.3) return "text-green-400";
  if (val <= -0.3) return "text-red-400";
  return "text-yellow-400";
}

function sentimentLabel(val: number): string {
  if (val >= 0.5) return "Bullish";
  if (val >= 0.2) return "Slightly Bullish";
  if (val <= -0.5) return "Bearish";
  if (val <= -0.2) return "Slightly Bearish";
  return "Neutral";
}

export default function SentimentGauge({ sentiments }: SentimentGaugeProps) {
  if (sentiments.length === 0) {
    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          Sentiment
        </h3>
        <p className="text-gray-500 text-sm">No sentiment data available</p>
      </div>
    );
  }

  const avgSentiment =
    sentiments.reduce((sum, s) => sum + (s.sentiment ?? 0), 0) /
    sentiments.length;
  const avgConfidence =
    sentiments.reduce((sum, s) => sum + (s.confidence ?? 0), 0) /
    sentiments.length;

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
        Sentiment
      </h3>

      <div className="flex items-baseline gap-2 mb-4">
        <span className={`text-3xl font-bold font-mono ${sentimentColor(avgSentiment)}`}>
          {avgSentiment >= 0 ? "+" : ""}
          {avgSentiment.toFixed(2)}
        </span>
        <span className={`text-sm ${sentimentColor(avgSentiment)}`}>
          {sentimentLabel(avgSentiment)}
        </span>
      </div>

      <div className="text-xs text-gray-500 mb-4">
        Confidence: {(avgConfidence * 100).toFixed(0)}% | {sentiments.length}{" "}
        headlines analyzed
      </div>

      <div className="space-y-2 max-h-48 overflow-y-auto">
        {sentiments.slice(0, 10).map((s, i) => (
          <div key={i} className="text-xs border-l-2 border-gray-700 pl-2">
            <div className="flex justify-between">
              <span className="text-gray-400 truncate max-w-[80%]">
                {s.headline || "No headline"}
              </span>
              <span
                className={`font-mono ${sentimentColor(s.sentiment ?? 0)}`}
              >
                {(s.sentiment ?? 0) >= 0 ? "+" : ""}
                {(s.sentiment ?? 0).toFixed(2)}
              </span>
            </div>
            {s.source && (
              <span className="text-gray-600">{s.source} | {s.date}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
