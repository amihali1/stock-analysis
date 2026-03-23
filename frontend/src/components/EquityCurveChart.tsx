"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  type IChartApi,
  type LineData,
  type Time,
  ColorType,
} from "lightweight-charts";
import type { DailyEquityPoint } from "@/lib/types";

interface EquityCurveChartProps {
  data: DailyEquityPoint[];
  height?: number;
}

export default function EquityCurveChart({
  data,
  height = 300,
}: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0a0a0a" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      width: containerRef.current.clientWidth,
      height,
      crosshair: {
        mode: 0,
      },
      timeScale: {
        borderColor: "#374151",
      },
    });
    chartRef.current = chart;

    // Cumulative P&L line (blue)
    const pnlSeries = chart.addLineSeries({
      color: "#3b82f6",
      lineWidth: 2,
      title: "Cumulative P&L",
    });
    const pnlData: LineData[] = data.map((d) => ({
      time: d.date as Time,
      value: d.cumulative_pnl,
    }));
    pnlSeries.setData(pnlData);

    // Total equity line (green)
    const equitySeries = chart.addLineSeries({
      color: "#22c55e",
      lineWidth: 2,
      title: "Total Equity",
    });
    const equityData: LineData[] = data.map((d) => ({
      time: d.date as Time,
      value: d.total_equity,
    }));
    equitySeries.setData(equityData);

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [data, height]);

  if (!data || data.length === 0) {
    return (
      <div className="bg-gray-900 rounded-lg p-4 text-gray-500 text-center text-sm">
        No equity data available.
      </div>
    );
  }

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden" />;
}
