"use client";

import { useEffect, useState } from "react";
import {
  getAlerts,
  getAlertSettings,
  saveAlertSetting,
  deleteAlertSetting,
  testAlertSetting,
  acknowledgeAlert,
  acknowledgeAllAlerts,
} from "@/lib/api";
import type { AlertEntry, AlertSetting } from "@/lib/types";

type Tab = "history" | "settings";

const TYPE_COLORS: Record<string, string> = {
  stop_loss: "bg-red-900/50 text-red-300 border-red-800",
  target_hit: "bg-green-900/50 text-green-300 border-green-800",
  high_conviction: "bg-yellow-900/50 text-yellow-300 border-yellow-800",
  position_closed: "bg-blue-900/50 text-blue-300 border-blue-800",
};

function typeLabel(t: string): string {
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function AlertsPage() {
  const [tab, setTab] = useState<Tab>("history");
  const [alerts, setAlerts] = useState<AlertEntry[]>([]);
  const [settings, setSettings] = useState<AlertSetting[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Settings form
  const [channel, setChannel] = useState("discord");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [scoreThreshold, setScoreThreshold] = useState(0.75);
  const [alertStopLoss, setAlertStopLoss] = useState(true);
  const [alertTargetHit, setAlertTargetHit] = useState(true);
  const [alertHighConviction, setAlertHighConviction] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);

  async function loadAlerts() {
    try {
      const data = await getAlerts();
      setAlerts(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    }
  }

  async function loadSettings() {
    try {
      const data = await getAlertSettings();
      setSettings(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings");
    }
  }

  useEffect(() => {
    Promise.all([loadAlerts(), loadSettings()]).finally(() =>
      setLoading(false)
    );
  }, []);

  async function handleAcknowledge(id: number) {
    try {
      await acknowledgeAlert(id);
      setAlerts((prev) =>
        prev.map((a) => (a.id === id ? { ...a, acknowledged: true } : a))
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to acknowledge");
    }
  }

  async function handleAcknowledgeAll() {
    try {
      await acknowledgeAllAlerts();
      setAlerts((prev) => prev.map((a) => ({ ...a, acknowledged: true })));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to acknowledge all");
    }
  }

  async function handleSaveSetting() {
    setSaving(true);
    setError(null);
    try {
      await saveAlertSetting({
        channel,
        webhook_url: channel === "discord" ? webhookUrl : undefined,
        bot_token: channel === "telegram" ? botToken : undefined,
        chat_id: channel === "telegram" ? chatId : undefined,
        enabled: true,
        score_threshold: scoreThreshold,
        alert_stop_loss: alertStopLoss,
        alert_target_hit: alertTargetHit,
        alert_high_conviction: alertHighConviction,
      });
      await loadSettings();
      setWebhookUrl("");
      setBotToken("");
      setChatId("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save setting");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest(settingId: number) {
    setTesting(settingId);
    try {
      await testAlertSetting(settingId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test alert failed");
    } finally {
      setTesting(null);
    }
  }

  async function handleDelete(settingId: number) {
    try {
      await deleteAlertSetting(settingId);
      setSettings((prev) => prev.filter((s) => s.id !== settingId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  }

  const unreadCount = alerts.filter((a) => !a.acknowledged).length;

  if (loading) {
    return (
      <div className="text-gray-500 text-center py-20">Loading alerts...</div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Alerts</h1>

      {error && (
        <div className="text-red-400 bg-red-900/20 border border-red-800 rounded p-4 mb-4">
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-3 text-red-300 hover:text-white text-xs"
          >
            dismiss
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-gray-900 rounded-lg p-1 w-fit">
        <button
          onClick={() => setTab("history")}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            tab === "history"
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white"
          }`}
        >
          History
          {unreadCount > 0 && (
            <span className="ml-2 bg-red-600 text-white text-xs px-1.5 py-0.5 rounded-full">
              {unreadCount}
            </span>
          )}
        </button>
        <button
          onClick={() => setTab("settings")}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            tab === "settings"
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white"
          }`}
        >
          Settings
        </button>
      </div>

      {/* History Tab */}
      {tab === "history" && (
        <div>
          {unreadCount > 0 && (
            <div className="mb-4">
              <button
                onClick={handleAcknowledgeAll}
                className="bg-gray-700 hover:bg-gray-600 text-white text-sm px-4 py-2 rounded transition-colors"
              >
                Acknowledge All ({unreadCount})
              </button>
            </div>
          )}

          {alerts.length === 0 ? (
            <div className="text-gray-500 text-center py-20">
              No alerts yet. Alerts will appear here when stop-losses, targets,
              or high-conviction signals are triggered.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-400 border-b border-gray-800 text-left">
                    <th className="py-3 px-3 w-8"></th>
                    <th className="py-3 px-3">Type</th>
                    <th className="py-3 px-3">Ticker</th>
                    <th className="py-3 px-3">Message</th>
                    <th className="py-3 px-3">Time</th>
                    <th className="py-3 px-3 w-20"></th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((alert) => (
                    <tr
                      key={alert.id}
                      className={`border-b border-gray-800/50 ${
                        !alert.acknowledged
                          ? "bg-gray-900/80"
                          : "opacity-60"
                      }`}
                    >
                      <td className="py-3 px-3">
                        {!alert.acknowledged && (
                          <div className="w-2 h-2 bg-blue-500 rounded-full" />
                        )}
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className={`text-xs px-2 py-0.5 rounded border ${
                            TYPE_COLORS[alert.alert_type] ||
                            "bg-gray-800 text-gray-300 border-gray-700"
                          }`}
                        >
                          {typeLabel(alert.alert_type)}
                        </span>
                      </td>
                      <td className="py-3 px-3 font-mono font-bold">
                        {alert.ticker}
                      </td>
                      <td className="py-3 px-3 text-gray-300 max-w-md truncate">
                        {alert.message}
                      </td>
                      <td className="py-3 px-3 text-gray-500 text-xs whitespace-nowrap">
                        {alert.created_at
                          ? new Date(alert.created_at).toLocaleString()
                          : "—"}
                      </td>
                      <td className="py-3 px-3">
                        {!alert.acknowledged && (
                          <button
                            onClick={() => handleAcknowledge(alert.id)}
                            className="text-blue-400 hover:text-blue-300 text-xs transition-colors"
                          >
                            Ack
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Settings Tab */}
      {tab === "settings" && (
        <div>
          {/* Existing settings */}
          {settings.length > 0 && (
            <div className="mb-6 space-y-3">
              {settings.map((s) => (
                <div
                  key={s.id}
                  className="bg-gray-900 rounded-lg p-4 flex items-center justify-between"
                >
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold capitalize">
                        {s.channel}
                      </span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          s.enabled
                            ? "bg-green-900/50 text-green-300"
                            : "bg-gray-800 text-gray-500"
                        }`}
                      >
                        {s.enabled ? "Active" : "Disabled"}
                      </span>
                    </div>
                    <div className="text-gray-500 text-xs space-x-3">
                      <span>
                        Threshold: {(s.score_threshold * 100).toFixed(0)}%
                      </span>
                      {s.alert_stop_loss && <span>Stop Loss</span>}
                      {s.alert_target_hit && <span>Target Hit</span>}
                      {s.alert_high_conviction && <span>High Conviction</span>}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleTest(s.id)}
                      disabled={testing === s.id}
                      className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-xs px-3 py-1.5 rounded transition-colors"
                    >
                      {testing === s.id ? "Sending..." : "Test"}
                    </button>
                    <button
                      onClick={() => handleDelete(s.id)}
                      className="text-red-400 hover:text-red-300 text-xs px-3 py-1.5 transition-colors"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Add new setting */}
          <div className="bg-gray-900 rounded-lg p-6">
            <h2 className="text-lg font-semibold mb-4">
              Add Alert Channel
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
                  Channel
                </label>
                <select
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
                >
                  <option value="discord">Discord</option>
                  <option value="telegram">Telegram</option>
                </select>
              </div>

              {channel === "discord" && (
                <div>
                  <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
                    Webhook URL
                  </label>
                  <input
                    type="text"
                    value={webhookUrl}
                    onChange={(e) => setWebhookUrl(e.target.value)}
                    placeholder="https://discord.com/api/webhooks/..."
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500"
                  />
                </div>
              )}

              {channel === "telegram" && (
                <>
                  <div>
                    <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
                      Bot Token
                    </label>
                    <input
                      type="text"
                      value={botToken}
                      onChange={(e) => setBotToken(e.target.value)}
                      placeholder="123456:ABC-DEF..."
                      className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500"
                    />
                  </div>
                  <div>
                    <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
                      Chat ID
                    </label>
                    <input
                      type="text"
                      value={chatId}
                      onChange={(e) => setChatId(e.target.value)}
                      placeholder="-1001234567890"
                      className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500"
                    />
                  </div>
                </>
              )}
            </div>

            {/* Alert type toggles */}
            <div className="mb-4">
              <label className="block text-gray-400 text-xs uppercase tracking-wider mb-2">
                Alert Types
              </label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alertStopLoss}
                    onChange={(e) => setAlertStopLoss(e.target.checked)}
                    className="rounded border-gray-600 bg-gray-800"
                  />
                  Stop Loss
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alertTargetHit}
                    onChange={(e) => setAlertTargetHit(e.target.checked)}
                    className="rounded border-gray-600 bg-gray-800"
                  />
                  Target Hit
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alertHighConviction}
                    onChange={(e) => setAlertHighConviction(e.target.checked)}
                    className="rounded border-gray-600 bg-gray-800"
                  />
                  High Conviction
                </label>
              </div>
            </div>

            {/* Score threshold slider */}
            <div className="mb-6">
              <label className="block text-gray-400 text-xs uppercase tracking-wider mb-1">
                Score Threshold: {(scoreThreshold * 100).toFixed(0)}%
              </label>
              <input
                type="range"
                min={0.5}
                max={1}
                step={0.05}
                value={scoreThreshold}
                onChange={(e) => setScoreThreshold(Number(e.target.value))}
                className="w-full max-w-xs"
              />
            </div>

            <button
              onClick={handleSaveSetting}
              disabled={
                saving ||
                (channel === "discord" && !webhookUrl.trim()) ||
                (channel === "telegram" &&
                  (!botToken.trim() || !chatId.trim()))
              }
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm px-6 py-2 rounded transition-colors"
            >
              {saving ? "Saving..." : "Save Channel"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
