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
  workspace?: string
  target_repo?: string
  planning_source?: string
  engineering_source?: string
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
  derived_related_pr_numbers?: number[]
}

export type GithubIssueEvidence = {
  number: number
  title?: string
  state?: string
  labels?: any
  updated_at?: string
  html_url?: string
}

export type GithubPrEvidence = {
  number: number
  title?: string
  state?: string
  draft?: boolean | null
  updated_at?: string
  html_url?: string
}

export type CiEvidence = {
  source: string
  status: string
  name: string
  summary?: string
  html_url?: string | null
  updated_at?: string
  pr_number?: number | null
}

export type IssuePrLink = {
  issue_number: number
  related_pr_numbers?: number[]
  link_sources?: Record<string, string[]>
  primary_pr_number?: number | null
  primary_link_basis?: string
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
  risk_summary?: string
  risk_drivers: string[]
  recommendations: string[]
  high_risk_subtasks: Array<Record<string, any>>
  subtasks: ProjectSubtask[]
  issue_pr_links?: IssuePrLink[]
  github_issue_evidence: GithubIssueEvidence[]
  github_pr_evidence: GithubPrEvidence[]
  ci_evidence?: CiEvidence[]
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

export type AgentToolCall = {
  name: string
  arguments?: Record<string, any>
  status?: string
  summary?: string
  coral_sources_used?: string[]
}

export type AgentAskResponse = {
  answer: string
  confidence: 'LOW' | 'MEDIUM' | 'HIGH'
  tool_calls: AgentToolCall[]
  evidence_summary: string
  recommended_actions: string[]
  reminder: Record<string, any> | null
  email_draft: Record<string, any> | null
  used_gemini: boolean
  fallback_used: boolean
  fallback_reason?: string
  gemini_model?: string
}

export type LatestActivityResponse = {
  status: string
  summary: string
  coral_sources_used: string[]
  data: {
    latest_pull_requests?: Array<Record<string, any>>
    latest_issues?: Array<Record<string, any>>
    latest_commits?: Array<Record<string, any>>
    pulls_status?: string
    issues_status?: string
    commits_status?: string
    pulls_summary?: string
    issues_summary?: string
    commits_summary?: string
    pulls_fallback_used?: boolean
    issues_fallback_used?: boolean
    pulls_data_origin?: string
    issues_data_origin?: string
    latest_pr_brief?: string
    high_risk_projects?: Array<Record<string, any>>
    recommended_actions?: string[]
  }
}

export async function fetchHealth() {
  return request<HealthResponse>('/api/health', { timeoutMs: 12000 })
}

export async function fetchProjects() {
  return request<ProjectReport[]>('/api/projects', { timeoutMs: 35000 })
}

export async function fetchOwners() {
  return request<OwnersResponse>('/api/owners', { timeoutMs: 40000 })
}

export async function fetchHighRiskReminders() {
  return request<RemindersResponse>('/api/reminders/high-risk', { timeoutMs: 20000 })
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
  return request<AgentAskResponse>('/api/agent/ask', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ question }),
    timeoutMs: 120000,
  })
}

export async function fetchLatestActivity(limit = 8) {
  return request<LatestActivityResponse>(`/api/activity/latest?limit=${encodeURIComponent(String(limit))}`, {
    timeoutMs: 45000,
  })
}

export async function syncPlanning() {
  const preferred = await optionalRequest<Record<string, any>>('/api/sync-planning', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (preferred) return preferred
  return optionalRequest<Record<string, any>>('/api/sync-roadmap', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({}),
  })
}
