'use client';

import { useEffect, useRef } from 'react';

interface SpreadLeg {
  option_type: string;
  action: string;
  strike: number;
  premium: number;
  contracts: number;
}

interface PLDiagramProps {
  legs: SpreadLeg[];
  currentPrice: number;
  contracts: number;
  maxProfit: number;
  maxLoss: number;
  breakeven: number | number[];
}

export default function PLDiagram({ legs, currentPrice, contracts, maxProfit, maxLoss, breakeven }: PLDiagramProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const padding = { top: 30, right: 30, bottom: 40, left: 60 };

    // Clear
    ctx.fillStyle = '#111827';
    ctx.fillRect(0, 0, width, height);

    // Calculate price range
    const strikes = legs.map(l => l.strike);
    const minStrike = Math.min(...strikes);
    const maxStrike = Math.max(...strikes);
    const priceRange = maxStrike - minStrike;
    const minPrice = minStrike - priceRange * 0.5;
    const maxPrice = maxStrike + priceRange * 0.5;

    // Calculate P&L for each price point
    const numPoints = 200;
    const points: { price: number; pnl: number }[] = [];

    for (let i = 0; i <= numPoints; i++) {
      const price = minPrice + (maxPrice - minPrice) * (i / numPoints);
      let pnl = 0;

      for (const leg of legs) {
        let legPnl: number;
        if (leg.option_type === 'call') {
          const intrinsic = Math.max(0, price - leg.strike);
          legPnl = (intrinsic - leg.premium) * 100 * leg.contracts;
        } else {
          const intrinsic = Math.max(0, leg.strike - price);
          legPnl = (intrinsic - leg.premium) * 100 * leg.contracts;
        }

        if (leg.action === 'sell') {
          // Seller collects premium, pays out intrinsic
          legPnl = (leg.premium * 100 * leg.contracts) - (leg.option_type === 'call'
            ? Math.max(0, price - leg.strike) * 100 * leg.contracts
            : Math.max(0, leg.strike - price) * 100 * leg.contracts);
        }

        pnl += legPnl;
      }

      points.push({ price, pnl });
    }

    const pnlValues = points.map(p => p.pnl);
    const minPnl = Math.min(...pnlValues);
    const maxPnl = Math.max(...pnlValues);
    const pnlRange = maxPnl - minPnl || 1;

    // Scale functions
    const scaleX = (price: number) =>
      padding.left + ((price - minPrice) / (maxPrice - minPrice)) * (width - padding.left - padding.right);
    const scaleY = (pnl: number) =>
      height - padding.bottom - ((pnl - minPnl) / pnlRange) * (height - padding.top - padding.bottom);

    // Draw zero line
    if (minPnl < 0 && maxPnl > 0) {
      const zeroY = scaleY(0);
      ctx.strokeStyle = '#4b5563';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(padding.left, zeroY);
      ctx.lineTo(width - padding.right, zeroY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Draw current price vertical line
    const currentX = scaleX(currentPrice);
    ctx.strokeStyle = '#6b7280';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(currentX, padding.top);
    ctx.lineTo(currentX, height - padding.bottom);
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw P&L line
    ctx.beginPath();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2;
    for (let i = 0; i < points.length; i++) {
      const x = scaleX(points[i].price);
      const y = scaleY(points[i].pnl);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Fill profit area green, loss area red
    for (let i = 0; i < points.length - 1; i++) {
      const x1 = scaleX(points[i].price);
      const x2 = scaleX(points[i + 1].price);
      const zeroY = scaleY(0);
      const y1 = scaleY(points[i].pnl);
      const y2 = scaleY(points[i + 1].pnl);

      ctx.globalAlpha = 0.15;
      ctx.fillStyle = points[i].pnl >= 0 ? '#10b981' : '#ef4444';
      ctx.beginPath();
      ctx.moveTo(x1, zeroY);
      ctx.lineTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.lineTo(x2, zeroY);
      ctx.closePath();
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // Draw breakeven markers
    const breakevenArr = Array.isArray(breakeven) ? breakeven : [breakeven];
    for (const be of breakevenArr) {
      const beX = scaleX(be);
      ctx.fillStyle = '#fbbf24';
      ctx.beginPath();
      ctx.arc(beX, scaleY(0), 4, 0, Math.PI * 2);
      ctx.fill();
    }

    // Labels
    ctx.fillStyle = '#9ca3af';
    ctx.font = '11px monospace';
    ctx.textAlign = 'center';

    // X-axis labels
    for (let i = 0; i <= 4; i++) {
      const price = minPrice + (maxPrice - minPrice) * (i / 4);
      ctx.fillText(`$${price.toFixed(0)}`, scaleX(price), height - 10);
    }

    // Y-axis labels
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const pnl = minPnl + pnlRange * (i / 4);
      ctx.fillText(`$${pnl.toFixed(0)}`, padding.left - 5, scaleY(pnl) + 4);
    }

    // Title labels
    ctx.fillStyle = '#d1d5db';
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`Max Profit: $${maxProfit.toFixed(0)}`, padding.left, 15);
    ctx.fillStyle = '#ef4444';
    ctx.fillText(`Max Loss: $${maxLoss.toFixed(0)}`, width / 2, 15);

  }, [legs, currentPrice, contracts, maxProfit, maxLoss, breakeven]);

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-2">Profit/Loss Diagram</h3>
      <canvas
        ref={canvasRef}
        width={500}
        height={280}
        className="w-full"
        style={{ maxWidth: 500 }}
      />
    </div>
  );
}
