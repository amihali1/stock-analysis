"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type Time,
} from "lightweight-charts";
import type { PricePoint, IndicatorPoint } from "@/lib/types";

interface StockChartProps {
  prices: PricePoint[];
  indicators?: IndicatorPoint[];
  height?: number;
}

export default function StockChart({
  prices,
  indicators = [],
  height = 400,
}: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || prices.length === 0) return;

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

    // Candlestick series
    const candlestick = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderDownColor: "#ef4444",
      borderUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      wickUpColor: "#22c55e",
    });

    const candleData: CandlestickData[] = prices
      .filter((p) => p.open != null && p.close != null)
      .map((p) => ({
        time: p.date as Time,
        open: p.open!,
        high: p.high!,
        low: p.low!,
        close: p.close!,
      }));
    candlestick.setData(candleData);

    // Volume histogram
    const volume = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const volData: HistogramData[] = prices
      .filter((p) => p.volume != null)
      .map((p) => ({
        time: p.date as Time,
        value: p.volume!,
        color:
          p.close != null && p.open != null && p.close >= p.open
            ? "#22c55e40"
            : "#ef444440",
      }));
    volume.setData(volData);

    // Indicator overlays
    if (indicators.length > 0) {
      const indByDate = new Map(indicators.map((ind) => [ind.date, ind]));

      // SMA 50
      const sma50Data: LineData[] = [];
      // SMA 200
      const sma200Data: LineData[] = [];
      // Bollinger Bands
      const bbUpperData: LineData[] = [];
      const bbLowerData: LineData[] = [];

      for (const p of prices) {
        const ind = indByDate.get(p.date);
        if (!ind) continue;
        if (ind.sma_50 != null)
          sma50Data.push({ time: p.date as Time, value: ind.sma_50 });
        if (ind.sma_200 != null)
          sma200Data.push({ time: p.date as Time, value: ind.sma_200 });
        if (ind.bb_upper != null)
          bbUpperData.push({ time: p.date as Time, value: ind.bb_upper });
        if (ind.bb_lower != null)
          bbLowerData.push({ time: p.date as Time, value: ind.bb_lower });
      }

      if (sma50Data.length > 0) {
        const sma50 = chart.addLineSeries({
          color: "#3b82f6",
          lineWidth: 1,
          title: "SMA 50",
        });
        sma50.setData(sma50Data);
      }

      if (sma200Data.length > 0) {
        const sma200 = chart.addLineSeries({
          color: "#f59e0b",
          lineWidth: 1,
          title: "SMA 200",
        });
        sma200.setData(sma200Data);
      }

      if (bbUpperData.length > 0) {
        const bbUp = chart.addLineSeries({
          color: "#6b728080",
          lineWidth: 1,
          lineStyle: 2,
        });
        bbUp.setData(bbUpperData);
      }

      if (bbLowerData.length > 0) {
        const bbLow = chart.addLineSeries({
          color: "#6b728080",
          lineWidth: 1,
          lineStyle: 2,
        });
        bbLow.setData(bbLowerData);
      }
    }

    chart.timeScale().fitContent();

    // Resize handler
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
  }, [prices, indicators, height]);

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden" />;
}
