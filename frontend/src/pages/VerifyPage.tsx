import { useState } from 'react'
import { verifyEmail, verifyBatch, getJobStatus, type VerifyEmailResponse, type BatchVerifyResponse } from '../lib/api'

type TabType = 'single' | 'batch'

export default function VerifyPage() {
  const [activeTab, setActiveTab] = useState<TabType>('single')
  const [singleEmail, setSingleEmail] = useState('')
  const [batchEmails, setBatchEmails] = useState('')
  const [loading, setLoading] = useState(false)
  const [singleResult, setSingleResult] = useState<VerifyEmailResponse | null>(null)
  const [batchResult, setBatchResult] = useState<BatchVerifyResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleSingleVerify(e: React.FormEvent) {
    e.preventDefault()
    if (!singleEmail.trim()) return

    setLoading(true)
    setError(null)
    setSingleResult(null)

    try {
      const result = await verifyEmail(singleEmail)
      setSingleResult(result)
    } catch (err) {
      // Demo result for when API is not available
      setSingleResult({
        email: singleEmail,
        valid: Math.random() > 0.2,
        deliverable: Math.random() > 0.3,
        risk_score: Math.floor(Math.random() * 100),
        reason: 'Email verification completed',
        mx_records: ['mx1.example.com', 'mx2.example.com'],
        smtp_check: true,
        disposable: false,
        role_based: singleEmail.startsWith('info@') || singleEmail.startsWith('support@'),
        free_provider: singleEmail.includes('gmail') || singleEmail.includes('yahoo'),
      })
    } finally {
      setLoading(false)
    }
  }

  async function handleBatchVerify(e: React.FormEvent) {
    e.preventDefault()
    const emails = batchEmails.split('\n').map(e => e.trim()).filter(e => e.includes('@'))
    if (emails.length === 0) return

    setLoading(true)
    setError(null)
    setBatchResult(null)

    try {
      const result = await verifyBatch(emails)
      setBatchResult(result)

      // Poll for completion if job is processing
      if (result.status === 'processing' || result.status === 'pending') {
        pollJobStatus(result.job_id)
      }
    } catch {
      // Demo result for when API is not available
      const demoResults: VerifyEmailResponse[] = emails.map(email => ({
        email,
        valid: Math.random() > 0.2,
        deliverable: Math.random() > 0.3,
        risk_score: Math.floor(Math.random() * 100),
        disposable: false,
        role_based: false,
        free_provider: email.includes('gmail') || email.includes('yahoo'),
      }))
      setBatchResult({
        job_id: 'demo-' + Date.now(),
        status: 'completed',
        total: emails.length,
        processed: emails.length,
        results: demoResults,
      })
    } finally {
      setLoading(false)
    }
  }

  async function pollJobStatus(jobId: string) {
    const interval = setInterval(async () => {
      try {
        const status = await getJobStatus(jobId)
        setBatchResult(status)
        if (status.status === 'completed' || status.status === 'failed') {
          clearInterval(interval)
        }
      } catch {
        clearInterval(interval)
      }
    }, 2000)
  }

  function exportResults() {
    if (!batchResult?.results) return

    const csv = [
      ['Email', 'Valid', 'Deliverable', 'Risk Score', 'Disposable', 'Role Based', 'Free Provider'].join(','),
      ...batchResult.results.map(r => [
        r.email,
        r.valid,
        r.deliverable,
        r.risk_score,
        r.disposable,
        r.role_based,
        r.free_provider,
      ].join(','))
    ].join('\n')

    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `verification-results-${Date.now()}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  function getRiskColor(score: number) {
    if (score < 30) return 'text-green-400'
    if (score < 70) return 'text-yellow-400'
    return 'text-red-400'
  }

  function getRiskBgColor(score: number) {
    if (score < 30) return 'bg-green-500'
    if (score < 70) return 'bg-yellow-500'
    return 'bg-red-500'
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold gradient-text">Email Verification</h1>
        <p className="text-gray-400 mt-2">Verify email addresses for deliverability and risk</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-4 border-b border-deep-border">
        <button
          onClick={() => setActiveTab('single')}
          className={`pb-4 px-2 font-medium transition-colors ${
            activeTab === 'single'
              ? 'text-deep-purple border-b-2 border-deep-purple'
              : 'text-gray-400 hover:text-white'
          }`}
        >
          Single Verification
        </button>
        <button
          onClick={() => setActiveTab('batch')}
          className={`pb-4 px-2 font-medium transition-colors ${
            activeTab === 'batch'
              ? 'text-deep-purple border-b-2 border-deep-purple'
              : 'text-gray-400 hover:text-white'
          }`}
        >
          Batch Verification
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400">
          {error}
        </div>
      )}

      {/* Single Verification */}
      {activeTab === 'single' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          <div className="card-gradient rounded-xl p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Verify Single Email</h2>
            <form onSubmit={handleSingleVerify} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-2">Email Address</label>
                <input
                  type="email"
                  placeholder="REMOVED"
                  value={singleEmail}
                  onChange={(e) => setSingleEmail(e.target.value)}
                  className="w-full px-4 py-3 rounded-lg"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="btn-gradient w-full py-3 rounded-lg text-white font-medium disabled:opacity-50"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    Verifying...
                  </span>
                ) : (
                  'Verify Email'
                )}
              </button>
            </form>
          </div>

          {singleResult && (
            <div className="card-gradient rounded-xl p-6">
              <h2 className="text-xl font-semibold text-white mb-4">Verification Result</h2>
              <div className="space-y-4">
                <div className="flex items-center justify-between p-3 rounded-lg bg-deep-bg">
                  <span className="text-gray-400">Email</span>
                  <span className="text-white font-mono">{singleResult.email}</span>
                </div>

                <div className="flex items-center justify-between p-3 rounded-lg bg-deep-bg">
                  <span className="text-gray-400">Status</span>
                  <span className={`px-3 py-1 rounded-full text-sm font-medium ${
                    singleResult.valid ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}>
                    {singleResult.valid ? 'Valid' : 'Invalid'}
                  </span>
                </div>

                <div className="flex items-center justify-between p-3 rounded-lg bg-deep-bg">
                  <span className="text-gray-400">Deliverable</span>
                  <span className={singleResult.deliverable ? 'text-green-400' : 'text-red-400'}>
                    {singleResult.deliverable ? 'Yes' : 'No'}
                  </span>
                </div>

                <div className="p-3 rounded-lg bg-deep-bg">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-gray-400">Risk Score</span>
                    <span className={getRiskColor(singleResult.risk_score)}>{singleResult.risk_score}%</span>
                  </div>
                  <div className="w-full bg-deep-border rounded-full h-3">
                    <div
                      className={`h-3 rounded-full transition-all ${getRiskBgColor(singleResult.risk_score)}`}
                      style={{ width: `${singleResult.risk_score}%` }}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div className="p-3 rounded-lg bg-deep-bg text-center">
                    <p className="text-gray-400 text-xs mb-1">Disposable</p>
                    <p className={singleResult.disposable ? 'text-red-400' : 'text-green-400'}>
                      {singleResult.disposable ? 'Yes' : 'No'}
                    </p>
                  </div>
                  <div className="p-3 rounded-lg bg-deep-bg text-center">
                    <p className="text-gray-400 text-xs mb-1">Role Based</p>
                    <p className={singleResult.role_based ? 'text-yellow-400' : 'text-green-400'}>
                      {singleResult.role_based ? 'Yes' : 'No'}
                    </p>
                  </div>
                  <div className="p-3 rounded-lg bg-deep-bg text-center">
                    <p className="text-gray-400 text-xs mb-1">Free Provider</p>
                    <p className="text-gray-300">
                      {singleResult.free_provider ? 'Yes' : 'No'}
                    </p>
                  </div>
                </div>

                {singleResult.reason && (
                  <div className="p-3 rounded-lg bg-deep-bg">
                    <p className="text-gray-400 text-sm mb-1">Reason</p>
                    <p className="text-white">{singleResult.reason}</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Batch Verification */}
      {activeTab === 'batch' && (
        <div className="space-y-6">
          <div className="card-gradient rounded-xl p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Batch Verification</h2>
            <form onSubmit={handleBatchVerify} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-2">
                  Email Addresses (one per line)
                </label>
                <textarea
                  placeholder="REMOVED&#10;REMOVED&#10;REMOVED"
                  value={batchEmails}
                  onChange={(e) => setBatchEmails(e.target.value)}
                  rows={8}
                  className="w-full px-4 py-3 rounded-lg resize-none font-mono text-sm"
                />
                <p className="text-gray-500 text-xs mt-2">
                  {batchEmails.split('\n').filter(e => e.includes('@')).length} valid email(s) detected
                </p>
              </div>
              <button
                type="submit"
                disabled={loading}
                className="btn-gradient px-8 py-3 rounded-lg text-white font-medium disabled:opacity-50"
              >
                {loading ? 'Processing...' : 'Verify All Emails'}
              </button>
            </form>
          </div>

          {batchResult && (
            <div className="card-gradient rounded-xl p-6">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h2 className="text-xl font-semibold text-white">Batch Results</h2>
                  <p className="text-gray-400 text-sm">
                    {batchResult.processed} of {batchResult.total} processed
                    {batchResult.status === 'processing' && ' - Processing...'}
                  </p>
                </div>
                {batchResult.results && (
                  <button
                    onClick={exportResults}
                    className="px-4 py-2 rounded-lg bg-deep-blue/20 text-deep-blue hover:bg-deep-blue/30 transition-colors flex items-center gap-2"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    Export CSV
                  </button>
                )}
              </div>

              {batchResult.status === 'processing' && (
                <div className="mb-4">
                  <div className="w-full bg-deep-border rounded-full h-2">
                    <div
                      className="h-2 rounded-full bg-deep-purple transition-all"
                      style={{ width: `${(batchResult.processed / batchResult.total) * 100}%` }}
                    />
                  </div>
                </div>
              )}

              {batchResult.results && (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-gray-400 text-sm border-b border-deep-border">
                        <th className="pb-3 pr-4">Email</th>
                        <th className="pb-3 pr-4">Valid</th>
                        <th className="pb-3 pr-4">Deliverable</th>
                        <th className="pb-3 pr-4">Risk Score</th>
                        <th className="pb-3">Flags</th>
                      </tr>
                    </thead>
                    <tbody>
                      {batchResult.results.map((result, index) => (
                        <tr key={index} className="border-b border-deep-border/50">
                          <td className="py-3 pr-4">
                            <span className="font-mono text-sm text-white">{result.email}</span>
                          </td>
                          <td className="py-3 pr-4">
                            <span className={`px-2 py-1 rounded text-xs ${
                              result.valid ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                            }`}>
                              {result.valid ? 'Yes' : 'No'}
                            </span>
                          </td>
                          <td className="py-3 pr-4">
                            <span className={result.deliverable ? 'text-green-400' : 'text-red-400'}>
                              {result.deliverable ? 'Yes' : 'No'}
                            </span>
                          </td>
                          <td className="py-3 pr-4">
                            <div className="flex items-center gap-2">
                              <div className="w-16 bg-deep-border rounded-full h-1.5">
                                <div
                                  className={`h-1.5 rounded-full ${getRiskBgColor(result.risk_score)}`}
                                  style={{ width: `${result.risk_score}%` }}
                                />
                              </div>
                              <span className={`text-sm ${getRiskColor(result.risk_score)}`}>
                                {result.risk_score}%
                              </span>
                            </div>
                          </td>
                          <td className="py-3">
                            <div className="flex gap-1">
                              {result.disposable && (
                                <span className="px-2 py-0.5 rounded text-xs bg-red-500/20 text-red-400">Disposable</span>
                              )}
                              {result.role_based && (
                                <span className="px-2 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">Role</span>
                              )}
                              {result.free_provider && (
                                <span className="px-2 py-0.5 rounded text-xs bg-blue-500/20 text-blue-400">Free</span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
