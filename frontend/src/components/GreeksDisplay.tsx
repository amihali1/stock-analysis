'use client';

interface GreeksDisplayProps {
  delta: number;
  theta: number;
  vega: number;
  maxProfit: number;
  maxLoss: number;
  riskRewardRatio: number;
  netCredit: number;
  expiryDays: number;
  earningsWarning: boolean;
}

export default function GreeksDisplay({
  delta,
  theta,
  vega,
  maxProfit,
  maxLoss,
  riskRewardRatio,
  netCredit,
  expiryDays,
  earningsWarning,
}: GreeksDisplayProps) {
  const greeks = [
    {
      label: 'Delta',
      value: delta,
      description: 'Directional exposure',
      color: delta < 0 ? 'text-red-400' : 'text-green-400',
    },
    {
      label: 'Theta',
      value: theta,
      description: 'Daily time decay',
      color: theta > 0 ? 'text-green-400' : 'text-red-400',
    },
    {
      label: 'Vega',
      value: vega,
      description: 'Volatility sensitivity',
      color: vega > 0 ? 'text-blue-400' : 'text-purple-400',
    },
  ];

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">Greeks & Risk</h3>

      {earningsWarning && (
        <div className="bg-yellow-900/30 border border-yellow-700 rounded px-3 py-2 mb-3 text-yellow-300 text-sm">
          Warning: Expiry crosses an earnings date
        </div>
      )}

      <div className="grid grid-cols-3 gap-3 mb-4">
        {greeks.map((g) => (
          <div key={g.label} className="text-center">
            <div className="text-xs text-gray-500">{g.label}</div>
            <div className={`text-lg font-mono font-bold ${g.color}`}>
              {g.value >= 0 ? '+' : ''}{g.value.toFixed(4)}
            </div>
            <div className="text-xs text-gray-600">{g.description}</div>
          </div>
        ))}
      </div>

      <div className="border-t border-gray-800 pt-3">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-gray-500">Max Profit:</span>
            <span className="text-green-400 ml-2 font-mono">${maxProfit.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-500">Max Loss:</span>
            <span className="text-red-400 ml-2 font-mono">${maxLoss.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-500">Net Credit:</span>
            <span className={`ml-2 font-mono ${netCredit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              ${netCredit.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="text-gray-500">Risk/Reward:</span>
            <span className="text-gray-300 ml-2 font-mono">{riskRewardRatio.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-gray-500">Days to Expiry:</span>
            <span className="text-gray-300 ml-2 font-mono">{expiryDays}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
