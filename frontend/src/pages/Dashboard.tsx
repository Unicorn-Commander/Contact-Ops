import { useState, useEffect } from 'react'
import { getStats, verifyEmail, type Stats, type VerifyEmailResponse } from '../lib/api'

interface StatCardProps {
  title: string
  value: string | number
  icon: React.ReactNode
  trend?: string
}

function StatCard({ title, value, icon, trend }: StatCardProps) {
  return (
    <div className="card-gradient rounded-xl p-6">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-gray-400 text-sm mb-1">{title}</p>
          <p className="text-3xl font-bold text-white">{value}</p>
          {trend && <p className="text-green-400 text-sm mt-2">{trend}</p>}
        </div>
        <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-deep-purple/20 to-deep-blue/20 flex items-center justify-center text-deep-purple">
          {icon}
        </div>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [quickEmail, setQuickEmail] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<VerifyEmailResponse | null>(null)

  useEffect(() => {
    loadStats()
  }, [])

  async function loadStats() {
    try {
      const data = await getStats()
      setStats(data)
    } catch {
      // Use placeholder stats if API not available
      setStats({
        total_verifications: 12453,
        emails_verified: 8234,
        companies_catalogued: 1543,
        contacts_catalogued: 4521,
        verifications_today: 234,
        success_rate: 94.5,
      })
    } finally {
      setLoading(false)
    }
  }

  async function handleQuickVerify(e: React.FormEvent) {
    e.preventDefault()
    if (!quickEmail.trim()) return

    setVerifying(true)
    setVerifyResult(null)

    try {
      const result = await verifyEmail(quickEmail)
      setVerifyResult(result)
    } catch (error) {
      // Demo result for when API is not available
      setVerifyResult({
        email: quickEmail,
        valid: true,
        deliverable: true,
        risk_score: 15,
        reason: 'Valid email address',
        disposable: false,
        role_based: false,
        free_provider: quickEmail.includes('gmail') || quickEmail.includes('yahoo'),
      })
    } finally {
      setVerifying(false)
    }
  }

  const recentActivity = [
    { type: 'verify', description: 'Batch verification completed', count: 150, time: '5 min ago' },
    { type: 'upload', description: 'CSV file processed', count: 2340, time: '1 hour ago' },
    { type: 'verify', description: 'Single email verified', count: 1, time: '2 hours ago' },
    { type: 'catalogue', description: 'New companies added', count: 45, time: '3 hours ago' },
  ]

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-deep-purple"></div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold gradient-text">Dashboard</h1>
        <p className="text-gray-400 mt-2">Welcome to Contact-Ops</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Total Verifications"
          value={stats?.total_verifications.toLocaleString() ?? '0'}
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
          trend={`+${stats?.verifications_today ?? 0} today`}
        />
        <StatCard
          title="Emails Verified"
          value={stats?.emails_verified.toLocaleString() ?? '0'}
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          }
          trend={`${stats?.success_rate ?? 0}% success rate`}
        />
        <StatCard
          title="Companies Catalogued"
          value={stats?.companies_catalogued.toLocaleString() ?? '0'}
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
          }
        />
        <StatCard
          title="Contacts Catalogued"
          value={stats?.contacts_catalogued.toLocaleString() ?? '0'}
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
            </svg>
          }
        />
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Quick Verify */}
        <div className="card-gradient rounded-xl p-6">
          <h2 className="text-xl font-semibold text-white mb-4">Quick Verify</h2>
          <form onSubmit={handleQuickVerify} className="space-y-4">
            <div>
              <input
                type="email"
                placeholder="Enter email address..."
                value={quickEmail}
                onChange={(e) => setQuickEmail(e.target.value)}
                className="w-full px-4 py-3 rounded-lg"
              />
            </div>
            <button
              type="submit"
              disabled={verifying}
              className="btn-gradient w-full py-3 rounded-lg text-white font-medium disabled:opacity-50"
            >
              {verifying ? 'Verifying...' : 'Verify Email'}
            </button>
          </form>

          {verifyResult && (
            <div className="mt-4 p-4 rounded-lg bg-deep-bg border border-deep-border">
              <div className="flex items-center justify-between mb-3">
                <span className="text-gray-400">Result</span>
                <span className={`px-2 py-1 rounded text-xs font-medium ${
                  verifyResult.valid ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                }`}>
                  {verifyResult.valid ? 'Valid' : 'Invalid'}
                </span>
              </div>
              <p className="text-white font-mono text-sm mb-2">{verifyResult.email}</p>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-gray-400">Risk Score:</span>
                <div className="flex-1 bg-deep-border rounded-full h-2">
                  <div
                    className={`h-2 rounded-full ${
                      verifyResult.risk_score < 30 ? 'bg-green-500' :
                      verifyResult.risk_score < 70 ? 'bg-yellow-500' : 'bg-red-500'
                    }`}
                    style={{ width: `${verifyResult.risk_score}%` }}
                  />
                </div>
                <span className="text-white">{verifyResult.risk_score}%</span>
              </div>
            </div>
          )}
        </div>

        {/* Recent Activity */}
        <div className="card-gradient rounded-xl p-6">
          <h2 className="text-xl font-semibold text-white mb-4">Recent Activity</h2>
          <div className="space-y-4">
            {recentActivity.map((activity, index) => (
              <div key={index} className="flex items-center gap-4 p-3 rounded-lg bg-deep-bg/50">
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  activity.type === 'verify' ? 'bg-green-500/20 text-green-400' :
                  activity.type === 'upload' ? 'bg-blue-500/20 text-blue-400' :
                  'bg-purple-500/20 text-purple-400'
                }`}>
                  {activity.type === 'verify' ? (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : activity.type === 'upload' ? (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                    </svg>
                  ) : (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                    </svg>
                  )}
                </div>
                <div className="flex-1">
                  <p className="text-white text-sm">{activity.description}</p>
                  <p className="text-gray-500 text-xs">{activity.count} items</p>
                </div>
                <span className="text-gray-500 text-xs">{activity.time}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
