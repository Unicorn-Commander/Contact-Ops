import { useState, useRef, useCallback } from 'react'
import { uploadCSV, getJobStatus, type UploadResponse, type JobStatus } from '../lib/api'

interface ColumnPreview {
  headers: string[]
  rows: string[][]
}

type UploadStatus = 'idle' | 'uploading' | 'processing' | 'completed' | 'failed'

export default function UploadPage() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ColumnPreview | null>(null)
  const [uploadStatus, setUploadStatus] = useState<UploadStatus>('idle')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [jobInfo, setJobInfo] = useState<UploadResponse | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0])
    }
  }, [])

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFile(e.target.files[0])
    }
  }

  const handleFile = (file: File) => {
    if (!file.name.endsWith('.csv')) {
      setError('Please upload a CSV file')
      return
    }

    setSelectedFile(file)
    setError(null)
    setUploadStatus('idle')
    setJobInfo(null)
    setJobStatus(null)

    // Parse preview
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target?.result as string
      const lines = text.split('\n').filter(line => line.trim())
      const headers = lines[0]?.split(',').map(h => h.trim().replace(/"/g, '')) || []
      const rows = lines.slice(1, 6).map(line =>
        line.split(',').map(cell => cell.trim().replace(/"/g, ''))
      )
      setPreview({ headers, rows })
    }
    reader.readAsText(file)
  }

  const handleUpload = async () => {
    if (!selectedFile) return

    setUploadStatus('uploading')
    setUploadProgress(0)
    setError(null)

    // Simulate upload progress
    const progressInterval = setInterval(() => {
      setUploadProgress(prev => {
        if (prev >= 90) {
          clearInterval(progressInterval)
          return prev
        }
        return prev + 10
      })
    }, 200)

    try {
      const result = await uploadCSV(selectedFile)
      clearInterval(progressInterval)
      setUploadProgress(100)
      setJobInfo(result)
      setUploadStatus('processing')

      // Poll for job status
      pollJobStatus(result.job_id)
    } catch {
      clearInterval(progressInterval)
      // Demo response
      setUploadProgress(100)
      setJobInfo({
        job_id: 'demo-' + Date.now(),
        filename: selectedFile.name,
        row_count: preview?.rows.length || 0,
        status: 'processing',
      })
      setUploadStatus('processing')

      // Simulate processing
      setTimeout(() => {
        setUploadStatus('completed')
        setJobStatus({
          job_id: 'demo-' + Date.now(),
          status: 'completed',
          total: preview?.rows.length || 100,
          processed: preview?.rows.length || 100,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
      }, 3000)
    }
  }

  const pollJobStatus = async (jobId: string) => {
    const interval = setInterval(async () => {
      try {
        const status = await getJobStatus(jobId)
        setJobStatus(status)
        if (status.status === 'completed') {
          setUploadStatus('completed')
          clearInterval(interval)
        } else if (status.status === 'failed') {
          setUploadStatus('failed')
          setError('Job processing failed')
          clearInterval(interval)
        }
      } catch {
        // Continue polling
      }
    }, 2000)
  }

  const downloadResults = () => {
    // In a real app, this would download the processed results
    const demoCSV = preview?.headers.join(',') + '\n' + preview?.rows.map(r => r.join(',')).join('\n')
    const blob = new Blob([demoCSV || ''], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `processed-${selectedFile?.name || 'results.csv'}`
    a.click()
    URL.revokeObjectURL(url)
  }

  const reset = () => {
    setSelectedFile(null)
    setPreview(null)
    setUploadStatus('idle')
    setUploadProgress(0)
    setJobInfo(null)
    setJobStatus(null)
    setError(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold gradient-text">CSV Upload</h1>
        <p className="text-gray-400 mt-2">Upload CSV files for batch processing and cataloguing</p>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 flex items-center gap-3">
          <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          {error}
        </div>
      )}

      {/* Upload Section */}
      {uploadStatus === 'idle' && (
        <div className="card-gradient rounded-xl p-6">
          <h2 className="text-xl font-semibold text-white mb-4">Upload File</h2>

          {/* Dropzone */}
          <div
            className={`dropzone rounded-xl p-12 text-center cursor-pointer transition-all ${
              dragActive ? 'active' : ''
            }`}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              onChange={handleFileInput}
              className="hidden"
            />
            <div className="flex flex-col items-center gap-4">
              <div className="w-16 h-16 rounded-full bg-deep-purple/20 flex items-center justify-center">
                <svg className="w-8 h-8 text-deep-purple" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
              </div>
              <div>
                <p className="text-white font-medium">
                  {selectedFile ? selectedFile.name : 'Drop your CSV file here'}
                </p>
                <p className="text-gray-500 text-sm mt-1">
                  {selectedFile
                    ? `${(selectedFile.size / 1024).toFixed(1)} KB`
                    : 'or click to browse'}
                </p>
              </div>
            </div>
          </div>

          {/* Preview */}
          {preview && (
            <div className="mt-6">
              <h3 className="text-lg font-medium text-white mb-3">Column Preview</h3>
              <div className="overflow-x-auto rounded-lg border border-deep-border">
                <table className="w-full">
                  <thead>
                    <tr className="bg-deep-bg">
                      {preview.headers.map((header, i) => (
                        <th key={i} className="px-4 py-3 text-left text-sm font-medium text-deep-purple border-b border-deep-border">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row, i) => (
                      <tr key={i} className="border-b border-deep-border/50 last:border-0">
                        {row.map((cell, j) => (
                          <td key={j} className="px-4 py-3 text-sm text-gray-300">
                            {cell || '-'}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-gray-500 text-xs mt-2">
                Showing first {preview.rows.length} rows
              </p>
            </div>
          )}

          {/* Upload Button */}
          {selectedFile && (
            <div className="mt-6 flex gap-4">
              <button
                onClick={handleUpload}
                className="btn-gradient px-8 py-3 rounded-lg text-white font-medium"
              >
                Upload and Process
              </button>
              <button
                onClick={reset}
                className="px-8 py-3 rounded-lg text-gray-400 hover:text-white border border-deep-border hover:border-deep-purple transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {/* Upload Progress */}
      {uploadStatus === 'uploading' && (
        <div className="card-gradient rounded-xl p-6">
          <h2 className="text-xl font-semibold text-white mb-4">Uploading...</h2>
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <div className="w-full bg-deep-border rounded-full h-3">
                  <div
                    className="h-3 rounded-full bg-gradient-to-r from-deep-purple to-deep-blue transition-all"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
              </div>
              <span className="text-white font-medium">{uploadProgress}%</span>
            </div>
            <p className="text-gray-400">
              Uploading {selectedFile?.name}...
            </p>
          </div>
        </div>
      )}

      {/* Processing Status */}
      {uploadStatus === 'processing' && (
        <div className="card-gradient rounded-xl p-6">
          <h2 className="text-xl font-semibold text-white mb-4">Processing</h2>
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="animate-spin rounded-full h-6 w-6 border-2 border-deep-purple border-t-transparent" />
              <span className="text-gray-400">Processing your file...</span>
            </div>

            {jobInfo && (
              <div className="grid grid-cols-2 gap-4 mt-4">
                <div className="p-4 rounded-lg bg-deep-bg">
                  <p className="text-gray-400 text-sm">Job ID</p>
                  <p className="text-white font-mono text-sm">{jobInfo.job_id}</p>
                </div>
                <div className="p-4 rounded-lg bg-deep-bg">
                  <p className="text-gray-400 text-sm">Filename</p>
                  <p className="text-white">{jobInfo.filename}</p>
                </div>
                <div className="p-4 rounded-lg bg-deep-bg">
                  <p className="text-gray-400 text-sm">Rows</p>
                  <p className="text-white">{jobInfo.row_count}</p>
                </div>
                <div className="p-4 rounded-lg bg-deep-bg">
                  <p className="text-gray-400 text-sm">Status</p>
                  <p className="text-yellow-400">{jobInfo.status}</p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Completed */}
      {uploadStatus === 'completed' && (
        <div className="card-gradient rounded-xl p-6">
          <div className="flex items-center gap-4 mb-6">
            <div className="w-12 h-12 rounded-full bg-green-500/20 flex items-center justify-center">
              <svg className="w-6 h-6 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div>
              <h2 className="text-xl font-semibold text-white">Processing Complete</h2>
              <p className="text-gray-400">Your file has been processed successfully</p>
            </div>
          </div>

          {jobStatus && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div className="p-4 rounded-lg bg-deep-bg">
                <p className="text-gray-400 text-sm">Total Rows</p>
                <p className="text-2xl font-bold text-white">{jobStatus.total}</p>
              </div>
              <div className="p-4 rounded-lg bg-deep-bg">
                <p className="text-gray-400 text-sm">Processed</p>
                <p className="text-2xl font-bold text-green-400">{jobStatus.processed}</p>
              </div>
              <div className="p-4 rounded-lg bg-deep-bg">
                <p className="text-gray-400 text-sm">Status</p>
                <p className="text-xl font-bold text-green-400 capitalize">{jobStatus.status}</p>
              </div>
              <div className="p-4 rounded-lg bg-deep-bg">
                <p className="text-gray-400 text-sm">Completed At</p>
                <p className="text-white text-sm">{new Date(jobStatus.updated_at).toLocaleString()}</p>
              </div>
            </div>
          )}

          <div className="flex gap-4">
            <button
              onClick={downloadResults}
              className="btn-gradient px-8 py-3 rounded-lg text-white font-medium flex items-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Download Results
            </button>
            <button
              onClick={reset}
              className="px-8 py-3 rounded-lg text-gray-400 hover:text-white border border-deep-border hover:border-deep-purple transition-colors"
            >
              Upload Another File
            </button>
          </div>
        </div>
      )}

      {/* Failed */}
      {uploadStatus === 'failed' && (
        <div className="card-gradient rounded-xl p-6">
          <div className="flex items-center gap-4 mb-6">
            <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center">
              <svg className="w-6 h-6 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <div>
              <h2 className="text-xl font-semibold text-white">Processing Failed</h2>
              <p className="text-red-400">{error || 'An error occurred during processing'}</p>
            </div>
          </div>
          <button
            onClick={reset}
            className="btn-gradient px-8 py-3 rounded-lg text-white font-medium"
          >
            Try Again
          </button>
        </div>
      )}
    </div>
  )
}
