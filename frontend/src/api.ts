const DEFAULT_TIMEOUT_MS = 15000

type RequestOptions = RequestInit & {
  timeoutMs?: number
}

async function parseErrorBody(resp: Response): Promise<string> {
  const raw = await resp.text().catch(() => '')
  if (!raw) return ''

  try {
    const json = JSON.parse(raw)
    if (typeof json === 'string') return json
    if (typeof json?.detail === 'string') return json.detail
    if (typeof json?.message === 'string') return json.message
    return raw.slice(0, 220)
  } catch {
    return raw.slice(0, 220)
  }
}

function buildError(path: string, status: number, detail: string): Error {
  const suffix = detail ? `: ${detail}` : ''
  return new Error(`${path} failed (${status})${suffix}`)
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const timeoutMs = typeof init?.timeoutMs === 'number' ? init.timeoutMs : DEFAULT_TIMEOUT_MS
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)
  const reqInit: RequestInit = { ...init, signal: controller.signal }
  delete (reqInit as any).timeoutMs

  try {
    const resp = await fetch(path, reqInit)
    if (!resp.ok) {
      const detail = await parseErrorBody(resp)
      throw buildError(path, resp.status, detail)
    }
    return resp.json()
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      throw new Error(`${path} timed out after ${timeoutMs}ms`)
    }
    if (err instanceof Error) throw err
    throw new Error(`${path} failed: network error`)
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function optionalRequest<T>(path: string, init?: RequestOptions): Promise<T | null> {
  const timeoutMs = typeof init?.timeoutMs === 'number' ? init.timeoutMs : DEFAULT_TIMEOUT_MS
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)
  const reqInit: RequestInit = { ...init, signal: controller.signal }
  delete (reqInit as any).timeoutMs

  try {
    const resp = await fetch(path, reqInit)
    if (resp.status === 404 || resp.status === 405) {
      return null
    }
    if (!resp.ok) {
      const detail = await parseErrorBody(resp)
      throw buildError(path, resp.status, detail)
    }
    return resp.json()
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      throw new Error(`${path} timed out after ${timeoutMs}ms`)
    }
    if (err instanceof Error) throw err
    throw new Error(`${path} failed: network error`)
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export type SourceInfo = {
  name: string
  table?: string
  status: 'connected' | 'missing'
}

export type HealthResponse = {
  product?: string
  backend: string
  coral: string
  mode: 'LIVE' | 'HYBRID' | 'NOT_READY'
  target_repo?: string
  demo_workspace?: string
  planning_source?: string
  engineering_source?: string
  demo_planning_source?: string
  demo_engineering_source?: string
  connected_sources_count: number
  sources: SourceInfo[]
}

export type ProjectSubtask = {
  subtask?: string
  status?: string
  assignee?: string
  estimated_completion_date?: string
  notes?: string
  github_issue_numbers?: number[]
  github_pr_numbers?: number[]
}

export type ProjectReport = {
  project_id: string
  project_name: string
  project_description?: string
  project_owner_lead?: string
  project_owner_contributor?: string
  contributor_email?: string
  can_email?: boolean
  planned_completion_date?: string
  project_status?: string
  total_subtasks: number
  completed_subtasks: number
  in_progress_subtasks: number
  blocked_subtasks: number
  linked_issue_count: number
  open_linked_issue_count: number
  linked_pr_count: number
  open_linked_pr_count: number
  stale_open_issue_count: number
  stale_open_pr_count: number
  all_github_issue_numbers?: number[]
  all_github_pr_numbers?: number[]
  risk_score: number
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  risk_drivers: string[]
  recommendations: string[]
  high_risk_subtasks: Array<Record<string, any>>
  subtasks: ProjectSubtask[]
  github_issue_evidence: Array<Record<string, any>>
  github_pr_evidence: Array<Record<string, any>>
  evidence_by_source: Record<string, any>
  coral_query_flow_used?: { steps?: string[]; queries?: string[] }
}

export type LeadOwnerRisk = {
  owner_lead: string
  total_projects_owned: number
  high_risk_projects: number
  contributors_needing_follow_up: string[]
  highest_risk_project?: string
  generated_reminder_count: number
}

export type ContributorOwnerRisk = {
  owner_contributor: string
  contributor_email?: string
  total_assigned_projects: number
  high_risk_assigned_projects: number
  blocked_subtasks: number
  open_linked_prs: number
  open_linked_issues: number
}

export type OwnersResponse = {
  leads: LeadOwnerRisk[]
  contributors: ContributorOwnerRisk[]
}

export type Reminder = {
  project_id: string
  project_name: string
  project_owner_lead?: string
  project_owner_contributor?: string
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  risk_score: number
  reason: string
  google_chat_text: string
  risk_drivers: string[]
  can_email?: boolean
  mailto_url?: string
  contributor_email?: string
}

export type RemindersResponse = {
  count: number
  reminders: Reminder[]
}

export type SyncStatusResponse = {
  status?: string
  last_synced_at?: string
  [key: string]: any
}

export async function fetchHealth() {
  return request<HealthResponse>('/api/health')
}

export async function fetchProjects() {
  return request<ProjectReport[]>('/api/projects')
}

export async function fetchOwners() {
  return request<OwnersResponse>('/api/owners')
}

export async function fetchHighRiskReminders() {
  return request<RemindersResponse>('/api/reminders/high-risk')
}

export async function generateReminders(payload: {
  risk_threshold?: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  project_id?: string | null
  owner_lead?: string | null
}) {
  return request<RemindersResponse>('/api/reminders/generate', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload || {}),
  })
}

export async function agentQuery(question: string) {
  return request<any>('/api/agent-query', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ question }),
  })
}

export async function fetchSyncStatus() {
  return optionalRequest<SyncStatusResponse>('/api/sync-status')
}

export async function syncRoadmap() {
  return optionalRequest<Record<string, any>>('/api/sync-roadmap', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({}),
  })
}
