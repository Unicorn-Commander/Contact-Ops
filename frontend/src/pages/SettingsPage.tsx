import { useState, useEffect } from 'react'

interface ApiKey {
  id: string
  key_prefix: string
  app_name: string
  description?: string
  tier: string
  rate_limit: number
  is_active: boolean
  expires_at?: string
  last_used_at?: string
  request_count: number
  created_at: string
}

interface NewKeyResponse {
  id: string
  key: string
  key_prefix: string
  app_name: string
  tier: string
  rate_limit: number
  expires_at?: string
  created_at: string
  warning: string
}

export default function SettingsPage() {
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newKey, setNewKey] = useState<NewKeyResponse | null>(null)
  const [copied, setCopied] = useState(false)

  // Form state
  const [appName, setAppName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)

  const fetchApiKeys = async () => {
    try {
      const response = await fetch('/api/v1/admin/api-keys')
      if (!response.ok) throw new Error('Failed to fetch API keys')
      const data = await response.json()
      setApiKeys(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load API keys')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchApiKeys()
  }, [])

  const createApiKey = async () => {
    if (!appName.trim()) return

    setCreating(true)
    try {
      const response = await fetch('/api/v1/admin/api-keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          app_name: appName,
          description: description || undefined,
          tier: 'standard',
          rate_limit: 10000,
        }),
      })

      if (!response.ok) throw new Error('Failed to create API key')

      const data: NewKeyResponse = await response.json()
      setNewKey(data)
      setShowCreateModal(false)
      setAppName('')
      setDescription('')
      fetchApiKeys()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create API key')
    } finally {
      setCreating(false)
    }
  }

  const revokeApiKey = async (keyId: string) => {
    if (!confirm('Are you sure you want to revoke this API key? This cannot be undone.')) {
      return
    }

    try {
      const response = await fetch(`/api/v1/admin/api-keys/${keyId}`, {
        method: 'DELETE',
      })

      if (!response.ok) throw new Error('Failed to revoke API key')

      fetchApiKeys()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke API key')
    }
  }

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-deep-purple"></div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-white">Settings</h1>
          <p className="text-gray-400 mt-1">Manage your API keys and account settings</p>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400">
          {error}
          <button onClick={() => setError(null)} className="ml-4 text-red-300 hover:text-red-200">
            Dismiss
          </button>
        </div>
      )}

      {/* New Key Display */}
      {newKey && (
        <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-6">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-full bg-green-500/20 flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-green-400">API Key Created Successfully!</h3>
              <p className="text-yellow-400 text-sm mt-1">
                Save this key now - it will not be shown again!
              </p>
              <div className="mt-4 bg-deep-bg rounded-lg p-4 flex items-center gap-3">
                <code className="text-green-300 font-mono text-sm flex-1 break-all">
                  {newKey.key}
                </code>
                <button
                  onClick={() => copyToClipboard(newKey.key)}
                  className="px-3 py-2 bg-deep-purple/30 hover:bg-deep-purple/50 rounded-lg text-white transition-colors flex-shrink-0"
                >
                  {copied ? 'Copied!' : 'Copy'}
                </button>
              </div>
              <button
                onClick={() => setNewKey(null)}
                className="mt-4 text-sm text-gray-400 hover:text-white"
              >
                I've saved my key, dismiss this message
              </button>
            </div>
          </div>
        </div>
      )}

      {/* API Keys Section */}
      <div className="bg-deep-card border border-deep-border rounded-xl overflow-hidden">
        <div className="p-6 border-b border-deep-border flex justify-between items-center">
          <div>
            <h2 className="text-lg font-semibold text-white">API Keys</h2>
            <p className="text-sm text-gray-400 mt-1">
              Use API keys to authenticate requests to the Contact-Ops API
            </p>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 bg-gradient-to-r from-deep-purple to-deep-blue rounded-lg text-white font-medium hover:opacity-90 transition-opacity flex items-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Create New Key
          </button>
        </div>

        {apiKeys.length === 0 ? (
          <div className="p-12 text-center">
            <div className="w-16 h-16 rounded-full bg-deep-border/50 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-white mb-2">No API keys yet</h3>
            <p className="text-gray-400 mb-6">Create an API key to start using the verification API</p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-4 py-2 bg-deep-purple/30 hover:bg-deep-purple/50 rounded-lg text-white transition-colors"
            >
              Create your first API key
            </button>
          </div>
        ) : (
          <div className="divide-y divide-deep-border">
            {apiKeys.map((key) => (
              <div key={key.id} className="p-6 flex items-center justify-between hover:bg-deep-border/20 transition-colors">
                <div className="flex items-center gap-4">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                    key.is_active ? 'bg-green-500/20' : 'bg-red-500/20'
                  }`}>
                    <svg className={`w-5 h-5 ${key.is_active ? 'text-green-400' : 'text-red-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                    </svg>
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="font-medium text-white">{key.app_name}</h3>
                      <code className="text-xs bg-deep-border/50 px-2 py-1 rounded text-gray-400">
                        {key.key_prefix}...
                      </code>
                      {!key.is_active && (
                        <span className="text-xs bg-red-500/20 text-red-400 px-2 py-1 rounded">
                          Revoked
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-400 mt-1">
                      {key.description || 'No description'}
                    </p>
                    <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                      <span>Created {formatDate(key.created_at)}</span>
                      {key.last_used_at && <span>Last used {formatDate(key.last_used_at)}</span>}
                      <span>{key.request_count.toLocaleString()} requests</span>
                    </div>
                  </div>
                </div>
                {key.is_active && (
                  <button
                    onClick={() => revokeApiKey(key.id)}
                    className="px-3 py-2 text-red-400 hover:bg-red-500/10 rounded-lg transition-colors text-sm"
                  >
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* API Documentation Link */}
      <div className="bg-deep-card border border-deep-border rounded-xl p-6">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-lg bg-deep-purple/20 flex items-center justify-center">
            <svg className="w-6 h-6 text-deep-purple" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <div className="flex-1">
            <h3 className="font-medium text-white">API Documentation</h3>
            <p className="text-sm text-gray-400">
              Learn how to integrate the Contact-Ops API into your applications
            </p>
          </div>
          <a
            href="/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="px-4 py-2 bg-deep-border/50 hover:bg-deep-border rounded-lg text-white transition-colors"
          >
            View Docs
          </a>
        </div>
      </div>

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-deep-card border border-deep-border rounded-xl w-full max-w-md p-6 mx-4">
            <h2 className="text-xl font-semibold text-white mb-4">Create New API Key</h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Application Name *
                </label>
                <input
                  type="text"
                  value={appName}
                  onChange={(e) => setAppName(e.target.value)}
                  placeholder="e.g., My CRM Integration"
                  className="w-full px-4 py-3 bg-deep-bg border border-deep-border rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-deep-purple"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Description (optional)
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What will this key be used for?"
                  rows={3}
                  className="w-full px-4 py-3 bg-deep-bg border border-deep-border rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-deep-purple resize-none"
                />
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => {
                  setShowCreateModal(false)
                  setAppName('')
                  setDescription('')
                }}
                className="flex-1 px-4 py-3 bg-deep-border/50 hover:bg-deep-border rounded-lg text-white transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={createApiKey}
                disabled={!appName.trim() || creating}
                className="flex-1 px-4 py-3 bg-gradient-to-r from-deep-purple to-deep-blue rounded-lg text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {creating ? 'Creating...' : 'Create Key'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
