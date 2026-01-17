const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

interface VerifyEmailResponse {
  email: string
  valid: boolean
  deliverable: boolean
  risk_score: number
  reason?: string
  mx_records?: string[]
  smtp_check?: boolean
  disposable?: boolean
  role_based?: boolean
  free_provider?: boolean
}

interface BatchVerifyResponse {
  job_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  total: number
  processed: number
  results?: VerifyEmailResponse[]
}

interface JobStatus {
  job_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  total: number
  processed: number
  results?: VerifyEmailResponse[]
  created_at: string
  updated_at: string
}

interface UploadResponse {
  job_id: string
  filename: string
  row_count: number
  status: string
}

interface Stats {
  total_verifications: number
  emails_verified: number
  companies_catalogued: number
  contacts_catalogued: number
  verifications_today: number
  success_rate: number
}

async function fetchAPI<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }))
    throw new Error(error.message || `HTTP ${response.status}`)
  }

  return response.json()
}

export async function verifyEmail(email: string): Promise<VerifyEmailResponse> {
  return fetchAPI<VerifyEmailResponse>('/verify/email', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export async function verifyBatch(emails: string[]): Promise<BatchVerifyResponse> {
  return fetchAPI<BatchVerifyResponse>('/verify/batch', {
    method: 'POST',
    body: JSON.stringify({ emails }),
  })
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return fetchAPI<JobStatus>(`/verify/job/${jobId}`)
}

export async function uploadCSV(file: File): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE}/catalogue/upload/csv`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Upload failed' }))
    throw new Error(error.message || `HTTP ${response.status}`)
  }

  return response.json()
}

export async function getStats(): Promise<Stats> {
  return fetchAPI<Stats>('/stats')
}

export type { VerifyEmailResponse, BatchVerifyResponse, JobStatus, UploadResponse, Stats }
