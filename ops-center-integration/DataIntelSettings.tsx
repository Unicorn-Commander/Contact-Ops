/**
 * Contact-Ops Settings Component for Ops-Center Admin Panel
 *
 * This component integrates into Ops-Center's Settings section to provide
 * full administrative control over Contact-Ops.
 *
 * Installation:
 * 1. Copy this file to ops-center/frontend/src/pages/admin/settings/DataIntelSettings.tsx
 * 2. Add route in ops-center routes configuration
 * 3. Add menu item in Settings sidebar
 *
 * Required environment variable in Ops-Center:
 *   VITE_CONTACT_OPS_API_URL=http://contact-ops-backend:8501
 */

import { useState, useEffect, useCallback } from 'react';

// Types
interface DashboardStats {
  total_companies: number;
  total_contacts: number;
  total_emails: number;
  verified_emails: number;
  valid_emails: number;
  invalid_emails: number;
  disposable_emails: number;
  verification_jobs: number;
  pending_jobs: number;
  source_apps_count: number;
  api_keys_count: number;
  last_updated: string;
}

interface SourceAppStats {
  source_app: string;
  companies: number;
  contacts: number;
  emails: number;
  last_activity: string | null;
}

interface ApiKeyInfo {
  id: string;
  key_prefix: string;
  app_name: string;
  description: string | null;
  tier: string;
  rate_limit: number;
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  request_count: number;
  created_at: string;
}

interface ApiKeyCreated {
  id: string;
  key: string;
  key_prefix: string;
  app_name: string;
  tier: string;
  rate_limit: number;
  expires_at: string | null;
  created_at: string;
  warning: string;
}

interface AppSettings {
  smtp_timeout: number;
  smtp_concurrency: number;
  max_batch_size: number;
  rate_limit_default: number;
  catch_all_detection: boolean;
  disposable_detection: boolean;
  role_based_detection: boolean;
}

interface RecentActivity {
  id: string;
  type: string;
  description: string;
  source_app: string | null;
  timestamp: string;
  details: Record<string, unknown>;
}

// API Configuration
const CONTACT_OPS_API_URL = import.meta.env.VITE_CONTACT_OPS_API_URL || 'http://localhost:8501';

// API Client
const api = {
  async get<T>(endpoint: string): Promise<T> {
    const response = await fetch(`${CONTACT_OPS_API_URL}${endpoint}`);
    if (!response.ok) throw new Error(`API error: ${response.statusText}`);
    return response.json();
  },

  async post<T>(endpoint: string, data?: unknown): Promise<T> {
    const response = await fetch(`${CONTACT_OPS_API_URL}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: data ? JSON.stringify(data) : undefined,
    });
    if (!response.ok) throw new Error(`API error: ${response.statusText}`);
    return response.json();
  },

  async put<T>(endpoint: string, data: unknown): Promise<T> {
    const response = await fetch(`${CONTACT_OPS_API_URL}${endpoint}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) throw new Error(`API error: ${response.statusText}`);
    return response.json();
  },

  async delete(endpoint: string): Promise<void> {
    const response = await fetch(`${CONTACT_OPS_API_URL}${endpoint}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error(`API error: ${response.statusText}`);
  },
};

// Stat Card Component
function StatCard({
  label,
  value,
  subtext,
  color = 'blue',
}: {
  label: string;
  value: number | string;
  subtext?: string;
  color?: 'blue' | 'green' | 'yellow' | 'red' | 'purple';
}) {
  const colorClasses = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    green: 'bg-green-50 border-green-200 text-green-700',
    yellow: 'bg-yellow-50 border-yellow-200 text-yellow-700',
    red: 'bg-red-50 border-red-200 text-red-700',
    purple: 'bg-purple-50 border-purple-200 text-purple-700',
  };

  return (
    <div className={`rounded-lg border p-4 ${colorClasses[color]}`}>
      <div className="text-sm font-medium opacity-75">{label}</div>
      <div className="text-2xl font-bold">{value.toLocaleString()}</div>
      {subtext && <div className="text-xs opacity-60 mt-1">{subtext}</div>}
    </div>
  );
}

// Main Component
export default function DataIntelSettings() {
  // State
  const [activeTab, setActiveTab] = useState<'overview' | 'apikeys' | 'settings' | 'activity'>('overview');
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [sourceStats, setSourceStats] = useState<SourceAppStats[]>([]);
  const [apiKeys, setApiKeys] = useState<ApiKeyInfo[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [recentActivity, setRecentActivity] = useState<RecentActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // New API Key form state
  const [showNewKeyForm, setShowNewKeyForm] = useState(false);
  const [newKeyForm, setNewKeyForm] = useState({
    app_name: '',
    description: '',
    tier: 'standard',
    rate_limit: 1000,
  });
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);

  // Fetch data
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statsData, sourceData, keysData, settingsData, activityData] = await Promise.all([
        api.get<DashboardStats>('/api/v1/admin/stats'),
        api.get<SourceAppStats[]>('/api/v1/admin/stats/by-source'),
        api.get<ApiKeyInfo[]>('/api/v1/admin/api-keys'),
        api.get<AppSettings>('/api/v1/admin/settings'),
        api.get<RecentActivity[]>('/api/v1/admin/activity/recent?limit=20'),
      ]);

      setStats(statsData);
      setSourceStats(sourceData);
      setApiKeys(keysData);
      setSettings(settingsData);
      setRecentActivity(activityData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Create API Key
  const handleCreateKey = async () => {
    try {
      const created = await api.post<ApiKeyCreated>('/api/v1/admin/api-keys', newKeyForm);
      setCreatedKey(created);
      setShowNewKeyForm(false);
      setNewKeyForm({ app_name: '', description: '', tier: 'standard', rate_limit: 1000 });
      fetchData(); // Refresh list
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create API key');
    }
  };

  // Revoke API Key
  const handleRevokeKey = async (keyId: string) => {
    if (!confirm('Are you sure you want to revoke this API key?')) return;
    try {
      await api.delete(`/api/v1/admin/api-keys/${keyId}`);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke API key');
    }
  };

  // Update Settings
  const handleUpdateSettings = async () => {
    if (!settings) return;
    try {
      await api.put('/api/v1/admin/settings', settings);
      alert('Settings saved successfully');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings');
    }
  };

  // Tab navigation
  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'apikeys', label: 'API Keys' },
    { id: 'settings', label: 'Settings' },
    { id: 'activity', label: 'Activity' },
  ] as const;

  if (loading && !stats) {
    return (
      <div className="p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-gray-200 rounded w-1/4"></div>
          <div className="grid grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-24 bg-gray-200 rounded"></div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Contact-Ops</h1>
          <p className="text-gray-500">Email verification and data cataloguing service</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
            Connected
          </span>
          <button
            onClick={fetchData}
            className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-md"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
          {error}
          <button onClick={() => setError(null)} className="ml-4 text-red-500 underline">
            Dismiss
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-4">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`py-2 px-1 border-b-2 font-medium text-sm transition-colors ${
                activeTab === tab.id
                  ? 'border-purple-500 text-purple-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && stats && (
        <div className="space-y-6">
          {/* Stats Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Companies" value={stats.total_companies} color="blue" />
            <StatCard label="Contacts" value={stats.total_contacts} color="purple" />
            <StatCard label="Emails Verified" value={stats.verified_emails} color="green" />
            <StatCard
              label="Valid Rate"
              value={
                stats.verified_emails > 0
                  ? `${Math.round((stats.valid_emails / stats.verified_emails) * 100)}%`
                  : 'N/A'
              }
              color="green"
            />
            <StatCard label="Invalid Emails" value={stats.invalid_emails} color="red" />
            <StatCard label="Disposable" value={stats.disposable_emails} color="yellow" />
            <StatCard label="Source Apps" value={stats.source_apps_count} color="blue" />
            <StatCard label="API Keys" value={stats.api_keys_count} color="purple" />
          </div>

          {/* Source Apps Breakdown */}
          <div className="bg-white rounded-lg border p-4">
            <h3 className="font-semibold text-gray-900 mb-4">Data by Source App</h3>
            {sourceStats.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-gray-500 border-b">
                      <th className="pb-2">App</th>
                      <th className="pb-2 text-right">Companies</th>
                      <th className="pb-2 text-right">Contacts</th>
                      <th className="pb-2 text-right">Emails</th>
                      <th className="pb-2 text-right">Last Activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sourceStats.map((source) => (
                      <tr key={source.source_app} className="border-b last:border-0">
                        <td className="py-2 font-medium">{source.source_app}</td>
                        <td className="py-2 text-right">{source.companies}</td>
                        <td className="py-2 text-right">{source.contacts}</td>
                        <td className="py-2 text-right">{source.emails}</td>
                        <td className="py-2 text-right text-gray-500">
                          {source.last_activity
                            ? new Date(source.last_activity).toLocaleDateString()
                            : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-gray-500">No source apps have submitted data yet.</p>
            )}
          </div>

          {/* Quick Verify */}
          <div className="bg-white rounded-lg border p-4">
            <h3 className="font-semibold text-gray-900 mb-4">Quick Verify</h3>
            <QuickVerifyForm />
          </div>
        </div>
      )}

      {/* API Keys Tab */}
      {activeTab === 'apikeys' && (
        <div className="space-y-4">
          {/* Created Key Alert */}
          {createdKey && (
            <div className="bg-yellow-50 border border-yellow-200 p-4 rounded-lg">
              <div className="flex items-start justify-between">
                <div>
                  <h4 className="font-semibold text-yellow-800">API Key Created</h4>
                  <p className="text-yellow-700 text-sm mt-1">{createdKey.warning}</p>
                  <div className="mt-2 font-mono bg-yellow-100 px-3 py-2 rounded text-sm break-all">
                    {createdKey.key}
                  </div>
                </div>
                <button
                  onClick={() => setCreatedKey(null)}
                  className="text-yellow-600 hover:text-yellow-800"
                >
                  &times;
                </button>
              </div>
            </div>
          )}

          {/* New Key Form */}
          {showNewKeyForm ? (
            <div className="bg-white rounded-lg border p-4">
              <h3 className="font-semibold mb-4">Create New API Key</h3>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">App Name</label>
                  <input
                    type="text"
                    value={newKeyForm.app_name}
                    onChange={(e) => setNewKeyForm({ ...newKeyForm, app_name: e.target.value })}
                    className="w-full px-3 py-2 border rounded-md"
                    placeholder="my-app"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Tier</label>
                  <select
                    value={newKeyForm.tier}
                    onChange={(e) => setNewKeyForm({ ...newKeyForm, tier: e.target.value })}
                    className="w-full px-3 py-2 border rounded-md"
                  >
                    <option value="standard">Standard</option>
                    <option value="premium">Premium</option>
                    <option value="unlimited">Unlimited</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Rate Limit</label>
                  <input
                    type="number"
                    value={newKeyForm.rate_limit}
                    onChange={(e) =>
                      setNewKeyForm({ ...newKeyForm, rate_limit: parseInt(e.target.value) })
                    }
                    className="w-full px-3 py-2 border rounded-md"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                  <input
                    type="text"
                    value={newKeyForm.description}
                    onChange={(e) => setNewKeyForm({ ...newKeyForm, description: e.target.value })}
                    className="w-full px-3 py-2 border rounded-md"
                    placeholder="Optional description"
                  />
                </div>
              </div>
              <div className="mt-4 flex gap-2">
                <button
                  onClick={handleCreateKey}
                  disabled={!newKeyForm.app_name}
                  className="px-4 py-2 bg-purple-600 text-white rounded-md hover:bg-purple-700 disabled:opacity-50"
                >
                  Create Key
                </button>
                <button
                  onClick={() => setShowNewKeyForm(false)}
                  className="px-4 py-2 bg-gray-100 rounded-md hover:bg-gray-200"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setShowNewKeyForm(true)}
              className="px-4 py-2 bg-purple-600 text-white rounded-md hover:bg-purple-700"
            >
              + Create API Key
            </button>
          )}

          {/* API Keys List */}
          <div className="bg-white rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left">App Name</th>
                  <th className="px-4 py-3 text-left">Key Prefix</th>
                  <th className="px-4 py-3 text-left">Tier</th>
                  <th className="px-4 py-3 text-right">Rate Limit</th>
                  <th className="px-4 py-3 text-right">Requests</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {apiKeys.map((key) => (
                  <tr key={key.id} className="border-t">
                    <td className="px-4 py-3 font-medium">{key.app_name}</td>
                    <td className="px-4 py-3 font-mono text-gray-500">{key.key_prefix}...</td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${
                          key.tier === 'unlimited'
                            ? 'bg-purple-100 text-purple-700'
                            : key.tier === 'premium'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-100 text-gray-700'
                        }`}
                      >
                        {key.tier}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">{key.rate_limit.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right">{key.request_count.toLocaleString()}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${
                          key.is_active ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                        }`}
                      >
                        {key.is_active ? 'Active' : 'Revoked'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {key.is_active && (
                        <button
                          onClick={() => handleRevokeKey(key.id)}
                          className="text-red-600 hover:text-red-800 text-sm"
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Settings Tab */}
      {activeTab === 'settings' && settings && (
        <div className="bg-white rounded-lg border p-6">
          <h3 className="font-semibold text-gray-900 mb-6">Verification Settings</h3>
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                SMTP Timeout (seconds)
              </label>
              <input
                type="number"
                value={settings.smtp_timeout}
                onChange={(e) =>
                  setSettings({ ...settings, smtp_timeout: parseInt(e.target.value) })
                }
                className="w-full px-3 py-2 border rounded-md"
                min={1}
                max={60}
              />
              <p className="text-xs text-gray-500 mt-1">Timeout for SMTP connections (1-60s)</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                SMTP Concurrency
              </label>
              <input
                type="number"
                value={settings.smtp_concurrency}
                onChange={(e) =>
                  setSettings({ ...settings, smtp_concurrency: parseInt(e.target.value) })
                }
                className="w-full px-3 py-2 border rounded-md"
                min={1}
                max={100}
              />
              <p className="text-xs text-gray-500 mt-1">Max concurrent SMTP verifications</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Batch Size</label>
              <input
                type="number"
                value={settings.max_batch_size}
                onChange={(e) =>
                  setSettings({ ...settings, max_batch_size: parseInt(e.target.value) })
                }
                className="w-full px-3 py-2 border rounded-md"
                min={1}
                max={10000}
              />
              <p className="text-xs text-gray-500 mt-1">Maximum emails per batch request</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Default Rate Limit
              </label>
              <input
                type="number"
                value={settings.rate_limit_default}
                onChange={(e) =>
                  setSettings({ ...settings, rate_limit_default: parseInt(e.target.value) })
                }
                className="w-full px-3 py-2 border rounded-md"
                min={1}
              />
              <p className="text-xs text-gray-500 mt-1">Default rate limit for new API keys</p>
            </div>

            <div className="md:col-span-2 space-y-3">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={settings.catch_all_detection}
                  onChange={(e) =>
                    setSettings({ ...settings, catch_all_detection: e.target.checked })
                  }
                  className="rounded"
                />
                <span className="text-sm">Enable catch-all domain detection</span>
              </label>

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={settings.disposable_detection}
                  onChange={(e) =>
                    setSettings({ ...settings, disposable_detection: e.target.checked })
                  }
                  className="rounded"
                />
                <span className="text-sm">Enable disposable email detection</span>
              </label>

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={settings.role_based_detection}
                  onChange={(e) =>
                    setSettings({ ...settings, role_based_detection: e.target.checked })
                  }
                  className="rounded"
                />
                <span className="text-sm">Enable role-based email detection</span>
              </label>
            </div>
          </div>

          <div className="mt-6">
            <button
              onClick={handleUpdateSettings}
              className="px-4 py-2 bg-purple-600 text-white rounded-md hover:bg-purple-700"
            >
              Save Settings
            </button>
          </div>
        </div>
      )}

      {/* Activity Tab */}
      {activeTab === 'activity' && (
        <div className="bg-white rounded-lg border overflow-hidden">
          <div className="p-4 border-b">
            <h3 className="font-semibold">Recent Activity</h3>
          </div>
          <div className="divide-y max-h-[600px] overflow-y-auto">
            {recentActivity.map((activity) => (
              <div key={activity.id} className="px-4 py-3 hover:bg-gray-50">
                <div className="flex items-center justify-between">
                  <div>
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs mr-2 ${
                        activity.type === 'verification'
                          ? 'bg-blue-100 text-blue-700'
                          : 'bg-green-100 text-green-700'
                      }`}
                    >
                      {activity.type}
                    </span>
                    <span className="text-sm">{activity.description}</span>
                  </div>
                  <span className="text-xs text-gray-500">
                    {new Date(activity.timestamp).toLocaleString()}
                  </span>
                </div>
                {activity.source_app && (
                  <div className="text-xs text-gray-500 mt-1">Source: {activity.source_app}</div>
                )}
              </div>
            ))}
            {recentActivity.length === 0 && (
              <div className="px-4 py-8 text-center text-gray-500">No recent activity</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Quick Verify Form Component
function QuickVerifyForm() {
  const [email, setEmail] = useState('');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  const handleVerify = async () => {
    if (!email) return;
    setLoading(true);
    setResult(null);
    try {
      const response = await fetch(`${CONTACT_OPS_API_URL}/api/v1/verify/email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const data = await response.json();
      setResult(data);
    } catch (err) {
      setResult({ error: err instanceof Error ? err.message : 'Verification failed' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="flex gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Enter email to verify"
          className="flex-1 px-3 py-2 border rounded-md"
          onKeyDown={(e) => e.key === 'Enter' && handleVerify()}
        />
        <button
          onClick={handleVerify}
          disabled={loading || !email}
          className="px-4 py-2 bg-purple-600 text-white rounded-md hover:bg-purple-700 disabled:opacity-50"
        >
          {loading ? 'Verifying...' : 'Verify'}
        </button>
      </div>
      {result && (
        <div className="mt-3 p-3 bg-gray-50 rounded-md">
          <pre className="text-xs overflow-x-auto">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
