import React, { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  agentQuery,
  fetchHealth,
  fetchHighRiskReminders,
  fetchOwners,
  fetchProjects,
  fetchSyncStatus,
  generateReminders,
  syncRoadmap,
  type HealthResponse,
  type LeadOwnerRisk,
  type OwnersResponse,
  type ProjectReport,
  type Reminder,
  type SyncStatusResponse,
} from './api'
import Button from './components/Button'
import Toast, { type ToastItem, type ToastTone } from './components/Toast'

type NavKey = 'overview' | 'projects' | 'owners' | 'reminders' | 'assistant' | 'technical'

type ChatMessage = {
  role: 'user' | 'assistant'
  text: string
}

const NAV_ITEMS: Array<{ key: NavKey; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'projects', label: 'Projects' },
  { key: 'owners', label: 'Owners' },
  { key: 'reminders', label: 'Reminders' },
  { key: 'assistant', label: 'Assistant' },
  { key: 'technical', label: 'Technical Evidence' },
]

const SUGGESTED_PROMPTS = [
  'Which projects are most at risk?',
  'Which owners need follow-up?',
  'Generate reminders for high-risk projects.',
  'Which projects are blocked?',
  'Which projects should not be bothered?',
  'What should I review first today?',
]

const RISK_LEVELS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const

function riskClass(level: string) {
  switch ((level || '').toUpperCase()) {
    case 'CRITICAL':
      return 'risk-badge critical'
    case 'HIGH':
      return 'risk-badge high'
    case 'MEDIUM':
      return 'risk-badge medium'
    default:
      return 'risk-badge low'
  }
}

function parseDateValue(value?: string | null): Date | null {
  if (!value) return null
  const raw = String(value).trim()
  const direct = new Date(raw)
  if (!Number.isNaN(direct.getTime())) return direct

  const currentYear = new Date().getFullYear()
  const withYear = new Date(`${raw} ${currentYear}`)
  if (!Number.isNaN(withYear.getTime())) return withYear
  return null
}

function prettyDate(value?: string | null): string {
  const dt = parseDateValue(value)
  if (!dt) return value || '-'
  return dt.toLocaleDateString()
}

function formatSyncTime(value?: string | null): string {
  if (!value) return new Date().toLocaleString()
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return value
  return dt.toLocaleString()
}

function unfinishedSubtasks(project: ProjectReport): number {
  return Math.max(0, (project.total_subtasks || 0) - (project.completed_subtasks || 0))
}

function isHighRisk(level?: string) {
  const normalized = (level || '').toUpperCase()
  return normalized === 'HIGH' || normalized === 'CRITICAL'
}

function parseEmail(text?: string | null): string | null {
  if (!text) return null
  const match = String(text).match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)
  return match ? match[0] : null
}

function resolveReminderEmail(reminder: Reminder): { canEmail: boolean; mailtoUrl: string | null } {
  if (reminder.can_email && reminder.mailto_url) {
    return { canEmail: true, mailtoUrl: reminder.mailto_url }
  }

  const explicitEmail = reminder.contributor_email || parseEmail(reminder.project_owner_contributor)
  if (!explicitEmail) {
    return { canEmail: false, mailtoUrl: null }
  }

  const subject = encodeURIComponent(`Sprint Tracker follow-up: ${reminder.project_name}`)
  const body = encodeURIComponent(reminder.google_chat_text || '')
  const mailtoUrl = `mailto:${explicitEmail}?subject=${subject}&body=${body}`
  return { canEmail: true, mailtoUrl }
}

function formatAgentResponse(data: any): string {
  if (!data) return 'No response.'
  if (typeof data.error === 'string') return `Unable to answer right now: ${data.error}`

  const lines: string[] = []
  if (typeof data.answer === 'string' && data.answer.trim()) {
    lines.push(data.answer.trim())
  }

  if (Array.isArray(data.priorities) && data.priorities.length > 0) {
    lines.push('Priority projects:')
    for (const item of data.priorities.slice(0, 5)) {
      lines.push(`- ${item.project_name} (${item.risk_level}, score ${item.risk_score})`)
    }
  }

  if (Array.isArray(data.projects) && data.projects.length > 0) {
    lines.push('Projects:')
    for (const item of data.projects.slice(0, 5)) {
      lines.push(`- ${item.project_name || '(unnamed project)'} (${item.risk_level || 'Risk unknown'})`)
    }
  }

  if (Array.isArray(data.owners) && data.owners.length > 0) {
    lines.push('Owners needing attention:')
    for (const item of data.owners.slice(0, 5)) {
      lines.push(`- ${item.owner}: avg risk ${item.average_risk}`)
    }
  }

  if (Array.isArray(data.contributors) && data.contributors.length > 0) {
    lines.push('Contributors:')
    for (const c of data.contributors.slice(0, 8)) {
      lines.push(`- ${c}`)
    }
  }

  if (Array.isArray(data.reminders) && data.reminders.length > 0) {
    lines.push('Reminder-ready projects:')
    for (const r of data.reminders.slice(0, 5)) {
      lines.push(`- ${r.project_name} (${r.risk_level})`)
    }
  }

  if (data.reminder && data.reminder.google_chat_text) {
    lines.push('Suggested reminder:')
    lines.push(data.reminder.google_chat_text)
  }

  if (lines.length === 0) return 'No operational summary available from the response.'
  return lines.join('\n')
}

export default function App() {
  const [activeNav, setActiveNav] = useState<NavKey>('overview')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [syncStatus, setSyncStatus] = useState<SyncStatusResponse | null>(null)
  const [projects, setProjects] = useState<ProjectReport[]>([])
  const [owners, setOwners] = useState<OwnersResponse>({ leads: [], contributors: [] })
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [selectedProject, setSelectedProject] = useState<ProjectReport | null>(null)
  const [selectedLead, setSelectedLead] = useState<LeadOwnerRisk | null>(null)

  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastSynced, setLastSynced] = useState<string>('')

  const [projectSearch, setProjectSearch] = useState('')
  const [projectRiskFilter, setProjectRiskFilter] = useState<'ALL' | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'>('ALL')
  const [projectOwnerFilter, setProjectOwnerFilter] = useState('ALL')
  const [projectSort, setProjectSort] = useState<'risk_desc' | 'risk_asc'>('risk_desc')

  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])

  const [isGeneratingReminders, setIsGeneratingReminders] = useState(false)
  const [generatingProjectId, setGeneratingProjectId] = useState<string | null>(null)
  const [copiedReminderIds, setCopiedReminderIds] = useState<Set<string>>(new Set())

  const [toasts, setToasts] = useState<ToastItem[]>([])

  const toastIdRef = useRef(1)
  const copyTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((item) => item.id !== id))
  }, [])

  const pushToast = useCallback(
    (message: string, tone: ToastTone = 'info', durationMs = 2400) => {
      const id = toastIdRef.current++
      setToasts((prev) => [...prev, { id, message, tone }])
      window.setTimeout(() => {
        dismissToast(id)
      }, durationMs)
    },
    [dismissToast],
  )

  useEffect(() => {
    return () => {
      copyTimersRef.current.forEach((timerId) => clearTimeout(timerId))
    }
  }, [])

  const markReminderCopied = useCallback(
    (key: string) => {
      setCopiedReminderIds((prev) => {
        const next = new Set(prev)
        next.add(key)
        return next
      })

      const existingTimer = copyTimersRef.current.get(key)
      if (existingTimer) clearTimeout(existingTimer)

      const timerId = setTimeout(() => {
        setCopiedReminderIds((prev) => {
          const next = new Set(prev)
          next.delete(key)
          return next
        })
        copyTimersRef.current.delete(key)
      }, 2000)

      copyTimersRef.current.set(key, timerId)
    },
    [],
  )

  const loadDashboardData = useCallback(
    async (silent = false) => {
      if (silent) {
        setRefreshing(true)
      } else {
        setLoading(true)
      }
      setError(null)

      const wrap = async <T,>(promise: Promise<T>): Promise<{ ok: true; value: T } | { ok: false; error: Error }> => {
        try {
          return { ok: true, value: await promise }
        } catch (err: any) {
          return { ok: false, error: err instanceof Error ? err : new Error('Unknown request error') }
        }
      }

      try {
        const backgroundRefresh = [
          wrap(fetchHealth()).then((healthResult) => {
            if (healthResult.ok) {
              setHealth(healthResult.value)
            } else {
              pushToast('Health status is delayed. Retrying on next refresh.', 'info', 2200)
            }
          }),
          wrap(fetchSyncStatus()).then((syncStatusResult) => {
            if (syncStatusResult.ok) {
              setSyncStatus(syncStatusResult.value)
              setLastSynced(formatSyncTime(syncStatusResult.value?.last_synced_at || new Date().toISOString()))
            } else {
              setLastSynced((prev) => prev || formatSyncTime(new Date().toISOString()))
            }
          }),
        ]

        const [projectsResult, ownersResult, remindersResult] = await Promise.all([
          wrap(fetchProjects()),
          wrap(fetchOwners()),
          wrap(fetchHighRiskReminders()),
        ])

        const failures: string[] = []

        if (projectsResult.ok) {
          setProjects(projectsResult.value || [])
        } else {
          failures.push(projectsResult.error.message)
        }

        if (ownersResult.ok) {
          setOwners(ownersResult.value || { leads: [], contributors: [] })
        } else {
          failures.push(ownersResult.error.message)
        }

        if (remindersResult.ok) {
          setReminders(remindersResult.value?.reminders || [])
        } else {
          failures.push(remindersResult.error.message)
        }

        if (!projectsResult.ok && projects.length === 0) {
          const message = projectsResult.error.message || 'Unable to load Sprint Tracker project data right now.'
          setError(message)
          pushToast('Failed to load projects. Please try again.', 'error', 3200)
        } else if (failures.length > 0) {
          setError(null)
          pushToast(`Some data is delayed (${failures.length} requests). Showing available results.`, 'info', 3000)
        }

        void Promise.all(backgroundRefresh)
      } finally {
        setLoading(false)
        setRefreshing(false)
      }
    },
    [projects.length, pushToast],
  )

  useEffect(() => {
    void loadDashboardData()
  }, [loadDashboardData])

  const mode = health?.mode || 'NOT_READY'
  const modeLabel = mode === 'NOT_READY' ? 'NOT READY' : mode
  const connectedSources = health?.connected_sources_count ?? 0

  const remindersByLead = useMemo(() => {
    const grouped: Record<string, Reminder[]> = {}
    for (const reminder of reminders) {
      const key = reminder.project_owner_lead || '(unassigned lead)'
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(reminder)
    }
    return Object.entries(grouped).sort((a, b) => a[0].localeCompare(b[0]))
  }, [reminders])

  const projectOwnerOptions = useMemo(() => {
    const items = new Set<string>()
    for (const project of projects) {
      if (project.project_owner_lead) items.add(project.project_owner_lead)
      if (project.project_owner_contributor) items.add(project.project_owner_contributor)
    }
    return ['ALL', ...Array.from(items).sort((a, b) => a.localeCompare(b))]
  }, [projects])

  const filteredProjects = useMemo(() => {
    let out = [...projects]

    const search = projectSearch.trim().toLowerCase()
    if (search) {
      out = out.filter((p) => {
        const haystack = [
          p.project_name,
          p.project_owner_lead,
          p.project_owner_contributor,
          p.project_status,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        return haystack.includes(search)
      })
    }

    if (projectRiskFilter !== 'ALL') {
      out = out.filter((p) => (p.risk_level || '').toUpperCase() === projectRiskFilter)
    }

    if (projectOwnerFilter !== 'ALL') {
      out = out.filter(
        (p) => p.project_owner_lead === projectOwnerFilter || p.project_owner_contributor === projectOwnerFilter,
      )
    }

    out.sort((a, b) => {
      const delta = (b.risk_score || 0) - (a.risk_score || 0)
      return projectSort === 'risk_desc' ? delta : -delta
    })

    return out
  }, [projects, projectSearch, projectRiskFilter, projectOwnerFilter, projectSort])

  const riskDistribution = useMemo(() => {
    const base: Record<'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW', number> = {
      CRITICAL: 0,
      HIGH: 0,
      MEDIUM: 0,
      LOW: 0,
    }
    for (const project of projects) {
      const level = (project.risk_level || 'LOW').toUpperCase() as keyof typeof base
      if (base[level] !== undefined) base[level] += 1
    }
    return base
  }, [projects])

  const topRisks = useMemo(() => {
    return [...projects].sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0)).slice(0, 5)
  }, [projects])

  const remindersByProjectId = useMemo(() => {
    const index = new Map<string, Reminder>()
    for (const reminder of reminders) {
      index.set(reminder.project_id, reminder)
    }
    return index
  }, [reminders])

  const overviewMetrics = useMemo(() => {
    const atRisk = projects.filter((p) => isHighRisk(p.risk_level)).length
    const blocked = projects.filter((p) => (p.blocked_subtasks || 0) > 0).length
    const openPRs = projects.reduce((sum, p) => sum + (p.open_linked_pr_count || 0), 0)
    const ownersNeedingAttention = owners.leads.filter(
      (lead) => lead.high_risk_projects > 0 || (lead.contributors_needing_follow_up || []).length > 0,
    ).length
    const remindersReady = reminders.length

    const followUpContributors = new Set<string>()
    for (const reminder of reminders) {
      if (reminder.project_owner_contributor) followUpContributors.add(reminder.project_owner_contributor)
    }

    const stalePRs = projects.reduce((sum, p) => sum + (p.stale_open_pr_count || 0), 0)
    const blockedSubtasks = projects.reduce((sum, p) => sum + (p.blocked_subtasks || 0), 0)
    const dueThisWeek = projects.filter((p) => {
      const date = parseDateValue(p.planned_completion_date)
      if (!date) return false
      const days = Math.floor((date.getTime() - Date.now()) / (1000 * 60 * 60 * 24))
      return days >= 0 && days <= 7 && unfinishedSubtasks(p) > 0
    }).length

    return {
      atRisk,
      blocked,
      openPRs,
      ownersNeedingAttention,
      remindersReady,
      followUpContributors: followUpContributors.size,
      stalePRs,
      blockedSubtasks,
      dueThisWeek,
    }
  }, [owners.leads, projects, reminders])

  const selectedLeadProjects = useMemo(() => {
    if (!selectedLead) return []
    return projects
      .filter((p) => (p.project_owner_lead || '(unassigned lead)') === selectedLead.owner_lead)
      .sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0))
  }, [projects, selectedLead])

  const copyText = useCallback(
    async (key: string, text: string) => {
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        window.prompt('Copy message:', text)
      }

      markReminderCopied(key)
      pushToast('Google Chat message copied', 'success')
    },
    [markReminderCopied, pushToast],
  )

  const openEmailDraft = useCallback(
    (reminder: Reminder) => {
      const emailMeta = resolveReminderEmail(reminder)
      if (!emailMeta.canEmail || !emailMeta.mailtoUrl) {
        pushToast('Contributor email unavailable', 'info')
        return
      }

      window.open(emailMeta.mailtoUrl, '_blank')
      pushToast('Email draft opened', 'success')
    },
    [pushToast],
  )

  const generateHighRiskReminders = useCallback(async () => {
    if (isGeneratingReminders) return
    setIsGeneratingReminders(true)

    try {
      const result = await generateReminders({ risk_threshold: 'HIGH', project_id: null, owner_lead: null })
      const generated = result.reminders || []
      setReminders(generated)

      if (generated.length > 0) {
        pushToast(`Generated ${generated.length} targeted reminders`, 'success')
      } else {
        pushToast('No high-risk reminders found', 'info')
      }
    } catch (err: any) {
      pushToast(err?.message || 'Failed to generate reminder', 'error', 3200)
    } finally {
      setIsGeneratingReminders(false)
    }
  }, [isGeneratingReminders, pushToast])

  const generateReminderForProject = useCallback(
    async (project: ProjectReport) => {
      if (!isHighRisk(project.risk_level)) {
        pushToast('No reminder needed — this project appears on track.', 'info')
        return
      }
      if (generatingProjectId) return

      setGeneratingProjectId(project.project_id)
      try {
        const result = await generateReminders({ risk_threshold: 'HIGH', project_id: project.project_id })
        const generated = result.reminders || []

        if (generated.length === 0) {
          pushToast('No reminder needed — this project appears on track.', 'info')
          return
        }

        setReminders((prev) => {
          const merged = new Map<string, Reminder>()
          for (const reminder of prev) merged.set(reminder.project_id, reminder)
          for (const reminder of generated) merged.set(reminder.project_id, reminder)
          return Array.from(merged.values())
        })

        pushToast('Reminder generated', 'success')
      } catch (err: any) {
        pushToast(err?.message || 'Failed to generate reminder', 'error', 3200)
      } finally {
        setGeneratingProjectId(null)
      }
    },
    [generatingProjectId, pushToast],
  )

  const handleRefresh = useCallback(async () => {
    if (refreshing) return

    try {
      const syncResult = await syncRoadmap()
      if (syncResult && syncResult.status) {
        pushToast(`Roadmap refreshed (${syncResult.status})`, 'success')
      } else {
        pushToast('Roadmap refreshed', 'success')
      }
    } catch (err: any) {
      pushToast(err?.message || 'Failed to refresh roadmap', 'error', 3200)
    }

    await loadDashboardData(true)
  }, [loadDashboardData, pushToast, refreshing])

  const sendAssistantQuestion = useCallback(
    async (question: string) => {
      const q = question.trim()
      if (!q || chatLoading) return

      setChatMessages((prev) => [...prev, { role: 'user', text: q }])
      setChatInput('')
      setChatLoading(true)

      try {
        const result = await agentQuery(q)
        setChatMessages((prev) => [...prev, { role: 'assistant', text: formatAgentResponse(result) }])
      } catch (err: any) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'assistant', text: `Unable to get assistant output: ${err?.message || 'Unknown error'}` },
        ])
      } finally {
        setChatLoading(false)
      }
    },
    [chatLoading],
  )

  const onChatSubmit = (e: FormEvent) => {
    e.preventDefault()
    void sendAssistantQuestion(chatInput)
  }

  const renderOverview = () => (
    <section className="page-section">
      <div className="kpi-grid">
        <article className="card kpi-card">
          <p className="kpi-label">At-risk projects</p>
          <p className="kpi-value">{overviewMetrics.atRisk}</p>
        </article>
        <article className="card kpi-card">
          <p className="kpi-label">Blocked projects</p>
          <p className="kpi-value">{overviewMetrics.blocked}</p>
        </article>
        <article className="card kpi-card">
          <p className="kpi-label">Open linked PRs</p>
          <p className="kpi-value">{overviewMetrics.openPRs}</p>
        </article>
        <article className="card kpi-card">
          <p className="kpi-label">Owners needing follow-up</p>
          <p className="kpi-value">{overviewMetrics.ownersNeedingAttention}</p>
        </article>
        <article className="card kpi-card">
          <p className="kpi-label">Reminders ready</p>
          <p className="kpi-value">{overviewMetrics.remindersReady}</p>
        </article>
      </div>

      <div className="overview-grid">
        <article className="card page-card">
          <h3 className="section-title">Current risk distribution</h3>
          <p className="muted-text">Snapshot of project risk levels across the workspace.</p>
          <div className="distribution-list">
            {RISK_LEVELS.map((level) => {
              const count = riskDistribution[level]
              const percent = projects.length ? Math.round((count / projects.length) * 100) : 0
              return (
                <div key={level} className="distribution-row">
                  <div className="distribution-label">
                    <span className={riskClass(level)}>{level}</span>
                    <span>{count}</span>
                  </div>
                  <div className="bar-track">
                    <div className={`bar-fill ${level.toLowerCase()}`} style={{ width: `${percent}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </article>

        <article className="card page-card">
          <h3 className="section-title">Recommended actions</h3>
          <ul className="action-list">
            <li>Follow up with {overviewMetrics.followUpContributors} contributors with reminder-ready projects.</li>
            <li>Review {overviewMetrics.stalePRs} stale open PRs linked to active projects.</li>
            <li>Resolve {overviewMetrics.blockedSubtasks} blocked subtasks to reduce delivery risk.</li>
            <li>Check {overviewMetrics.dueThisWeek} projects due this week with unfinished work.</li>
          </ul>
        </article>
      </div>

      <article className="card page-card">
        <div className="card-head">
          <h3 className="section-title">Top risks requiring action</h3>
          <Button variant="secondary" size="sm" onClick={() => setActiveNav('projects')}>
            View all projects
          </Button>
        </div>

        {topRisks.length === 0 && (
          <p className="empty-state">No projects found. Refresh planning data or check Coral source status.</p>
        )}

        {topRisks.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Owner lead</th>
                  <th>Contributor</th>
                  <th>Risk</th>
                  <th>Why it is risky</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {topRisks.map((project) => (
                  <tr key={project.project_id}>
                    <td>{project.project_name}</td>
                    <td>{project.project_owner_lead || '-'}</td>
                    <td>{project.project_owner_contributor || '-'}</td>
                    <td>
                      <div className="risk-stack">
                        <span className={riskClass(project.risk_level)}>{project.risk_level}</span>
                        <span className="risk-score">{project.risk_score}</span>
                      </div>
                    </td>
                    <td>{(project.risk_drivers || [])[0] || 'Needs attention'}</td>
                    <td>
                      <div className="action-row">
                        <Button size="sm" variant="secondary" onClick={() => setSelectedProject(project)}>
                          View details
                        </Button>
                        <Button
                          size="sm"
                          variant="primary"
                          loading={generatingProjectId === project.project_id}
                          disabled={!isHighRisk(project.risk_level)}
                          onClick={() => void generateReminderForProject(project)}
                        >
                          Generate reminder
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  )

  const renderProjects = () => (
    <section className="page-section">
      <article className="card page-card">
        <div className="card-head">
          <div>
            <h3 className="section-title">Projects</h3>
            <p className="muted-text">Monitor delivery health and focus on projects that need attention.</p>
          </div>
        </div>

        <div className="filter-row">
          <input
            placeholder="Search projects, owners, contributors..."
            value={projectSearch}
            onChange={(e) => setProjectSearch(e.target.value)}
          />
          <select value={projectRiskFilter} onChange={(e) => setProjectRiskFilter(e.target.value as any)}>
            <option value="ALL">All risk levels</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
          <select value={projectOwnerFilter} onChange={(e) => setProjectOwnerFilter(e.target.value)}>
            {projectOwnerOptions.map((owner) => (
              <option key={owner} value={owner}>
                {owner === 'ALL' ? 'All owners' : owner}
              </option>
            ))}
          </select>
          <select value={projectSort} onChange={(e) => setProjectSort(e.target.value as 'risk_desc' | 'risk_asc')}>
            <option value="risk_desc">Risk score (high to low)</option>
            <option value="risk_asc">Risk score (low to high)</option>
          </select>
        </div>

        {filteredProjects.length === 0 && (
          <p className="empty-state">No projects found. Refresh planning data or check Coral source status.</p>
        )}

        {filteredProjects.length > 0 && (
          <div className="table-wrap">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Owner Lead</th>
                  <th>Contributor</th>
                  <th>Status</th>
                  <th>Planned Date</th>
                  <th>Subtasks</th>
                  <th>Linked Issues</th>
                  <th>Linked PRs</th>
                  <th>Risk</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredProjects.map((project) => (
                  <tr key={project.project_id}>
                    <td>{project.project_name}</td>
                    <td>{project.project_owner_lead || '-'}</td>
                    <td>{project.project_owner_contributor || '-'}</td>
                    <td>{project.project_status || '-'}</td>
                    <td>{prettyDate(project.planned_completion_date)}</td>
                    <td>
                      {project.completed_subtasks}/{project.total_subtasks}
                    </td>
                    <td>{project.open_linked_issue_count ?? project.linked_issue_count ?? 0}</td>
                    <td>{project.open_linked_pr_count ?? project.linked_pr_count ?? 0}</td>
                    <td>
                      <div className="risk-stack">
                        <span className={riskClass(project.risk_level)}>{project.risk_level}</span>
                        <span className="risk-score">{project.risk_score}</span>
                      </div>
                    </td>
                    <td>
                      <div className="action-row">
                        <Button size="sm" variant="secondary" onClick={() => setSelectedProject(project)}>
                          View
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  )

  const renderOwners = () => (
    <section className="page-section">
      <article className="card page-card">
        <div className="card-head">
          <div>
            <h3 className="section-title">Owner leads</h3>
            <p className="muted-text">Identify which engineering leads and contributors require follow-up.</p>
          </div>
        </div>

        {owners.leads.length === 0 && <p className="empty-state">No owner risk data available yet.</p>}

        {owners.leads.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Owner lead</th>
                  <th>Total projects</th>
                  <th>High-risk projects</th>
                  <th>Contributors needing follow-up</th>
                  <th>Average risk</th>
                  <th>Highest-risk project</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {owners.leads.map((lead) => {
                  const leadProjects = projects.filter(
                    (p) => (p.project_owner_lead || '(unassigned lead)') === lead.owner_lead,
                  )
                  const avgRisk = leadProjects.length
                    ? Math.round(leadProjects.reduce((sum, p) => sum + (p.risk_score || 0), 0) / leadProjects.length)
                    : 0

                  return (
                    <tr key={lead.owner_lead}>
                      <td>{lead.owner_lead}</td>
                      <td>{lead.total_projects_owned}</td>
                      <td>{lead.high_risk_projects}</td>
                      <td>{(lead.contributors_needing_follow_up || []).join(', ') || '-'}</td>
                      <td>{avgRisk}</td>
                      <td>{lead.highest_risk_project || '-'}</td>
                      <td>
                        <div className="action-row">
                          <Button size="sm" variant="secondary" onClick={() => setSelectedLead(lead)}>
                            View owner details
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </article>

      <article className="card page-card">
        <h3 className="section-title">Contributors</h3>
        {owners.contributors.length === 0 && <p className="empty-state">No owner risk data available yet.</p>}

        {owners.contributors.length > 0 && (
          <div className="table-wrap">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Contributor</th>
                  <th>Total assigned projects</th>
                  <th>High-risk assigned projects</th>
                  <th>Blocked subtasks</th>
                  <th>Open linked issues</th>
                  <th>Open linked PRs</th>
                </tr>
              </thead>
              <tbody>
                {owners.contributors.map((owner) => (
                  <tr key={owner.owner_contributor}>
                    <td>{owner.owner_contributor}</td>
                    <td>{owner.total_assigned_projects}</td>
                    <td>{owner.high_risk_assigned_projects}</td>
                    <td>{owner.blocked_subtasks}</td>
                    <td>{owner.open_linked_issues}</td>
                    <td>{owner.open_linked_prs}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  )

  const renderReminders = () => (
    <section className="page-section">
      <article className="card page-card">
        <div className="card-head">
          <div>
            <h3 className="section-title">Targeted Google Chat Reminders</h3>
            <p className="muted-text">Only generate follow-ups for projects that need attention.</p>
          </div>
          <Button onClick={() => void generateHighRiskReminders()} loading={isGeneratingReminders}>
            Generate reminders
          </Button>
        </div>

        <div className="kpi-grid reminders-kpis">
          <article className="kpi-mini">
            <p>Reminders ready</p>
            <strong>{reminders.length}</strong>
          </article>
          <article className="kpi-mini">
            <p>Contributors affected</p>
            <strong>{new Set(reminders.map((r) => r.project_owner_contributor || '(unassigned contributor)')).size}</strong>
          </article>
          <article className="kpi-mini">
            <p>High-risk projects covered</p>
            <strong>{new Set(reminders.map((r) => r.project_id)).size}</strong>
          </article>
        </div>

        {reminders.length === 0 && (
          <p className="empty-state">No high-risk reminders needed right now.</p>
        )}
      </article>

      {remindersByLead.map(([lead, items]) => (
        <article key={lead} className="card page-card">
          <h4 className="section-title">{lead}</h4>
          <div className="reminder-grid">
            {items.map((reminder) => {
              const key = `${reminder.project_id}-${reminder.project_owner_contributor || 'na'}`
              const isCopied = copiedReminderIds.has(key)
              const emailMeta = resolveReminderEmail(reminder)

              return (
                <div key={key} className="reminder-card">
                  <div className="reminder-head">
                    <h5>{reminder.project_name}</h5>
                    <span className={riskClass(reminder.risk_level)}>{reminder.risk_level}</span>
                  </div>

                  <p>
                    <strong>Contributor:</strong> {reminder.project_owner_contributor || '-'}
                  </p>
                  <p>
                    <strong>Top risk drivers:</strong> {(reminder.risk_drivers || []).slice(0, 3).join(' | ') || '-'}
                  </p>

                  <pre>{reminder.google_chat_text}</pre>

                  <div className="action-row">
                    <Button
                      variant={isCopied ? 'success' : 'secondary'}
                      size="sm"
                      onClick={() => void copyText(key, reminder.google_chat_text)}
                    >
                      {isCopied ? 'Copied' : 'Copy Google Chat message'}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openEmailDraft(reminder)}
                      disabled={!emailMeta.canEmail}
                      title={emailMeta.canEmail ? 'Open email draft' : 'Contributor email unavailable'}
                    >
                      Open email draft
                    </Button>
                  </div>

                  {!emailMeta.canEmail && (
                    <p className="muted-text small-text">Contributor email unavailable</p>
                  )}
                </div>
              )
            })}
          </div>
        </article>
      ))}
    </section>
  )

  const renderAssistant = () => (
    <section className="page-section">
      <article className="card page-card">
        <div className="card-head">
          <div>
            <h3 className="section-title">Assistant</h3>
            <p className="muted-text">Ask for today’s priorities and follow-up actions.</p>
          </div>
        </div>

        <div className="prompt-list">
          {SUGGESTED_PROMPTS.map((prompt) => (
            <Button
              key={prompt}
              className="prompt-chip"
              variant="ghost"
              size="sm"
              onClick={() => void sendAssistantQuestion(prompt)}
              disabled={chatLoading}
            >
              {prompt}
            </Button>
          ))}
        </div>

        <div className="chat-log">
          {chatMessages.length === 0 && (
            <p className="empty-state">Ask Sprint Tracker which projects need attention.</p>
          )}

          {chatMessages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`chat-row ${message.role}`}>
              <strong>{message.role === 'user' ? 'You' : 'Assistant'}</strong>
              <pre>{message.text}</pre>
            </div>
          ))}

          {chatLoading && <div className="chat-row assistant">Working on it...</div>}
        </div>

        <form className="chat-form" onSubmit={onChatSubmit}>
          <input
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Ask: what should I review first today?"
          />
          <Button type="submit" loading={chatLoading}>
            Send
          </Button>
        </form>
      </article>
    </section>
  )

  const renderTechnicalEvidence = () => (
    <section className="page-section">
      <article className="card page-card">
        <h3 className="section-title">Coral and source status</h3>
        <div className="technical-grid">
          <div className="technical-item">
            <span className="muted-text">Mode</span>
            <strong>{modeLabel}</strong>
          </div>
          <div className="technical-item">
            <span className="muted-text">Connected sources</span>
            <strong>{connectedSources}</strong>
          </div>
          <div className="technical-item">
            <span className="muted-text">Last synced</span>
            <strong>{lastSynced || '-'}</strong>
          </div>
          <div className="technical-item">
            <span className="muted-text">Source status</span>
            <strong>{syncStatus?.status || 'Unknown'}</strong>
          </div>
        </div>
      </article>

      <article className="card page-card">
        <h3 className="section-title">Connected sources</h3>
        {!(health?.sources || []).length && <p className="empty-state">No source metadata available yet.</p>}

        {(health?.sources || []).length > 0 && (
          <div className="table-wrap">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Table</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(health?.sources || []).map((source) => (
                  <tr key={`${source.name}-${source.table || 'table'}`}>
                    <td>{source.name}</td>
                    <td>{source.table || '-'}</td>
                    <td>
                      <span className={source.status === 'connected' ? 'badge-ok' : 'badge-missing'}>
                        {source.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>

      <article className="card page-card">
        <h3 className="section-title">Coral query flow</h3>
        <ol className="evidence-list">
          <li>Query planning rows from Coral.</li>
          <li>Group rows into projects in backend.</li>
          <li>Extract linked issue and PR numbers.</li>
          <li>Fetch GitHub issues and PRs through Coral.</li>
          <li>Join evidence and compute project risk.</li>
          <li>Generate targeted reminder text for risky projects only.</li>
        </ol>
      </article>
    </section>
  )

  const renderPage = () => {
    if (activeNav === 'overview') return renderOverview()
    if (activeNav === 'projects') return renderProjects()
    if (activeNav === 'owners') return renderOwners()
    if (activeNav === 'reminders') return renderReminders()
    if (activeNav === 'assistant') return renderAssistant()
    return renderTechnicalEvidence()
  }

  const projectReminder = selectedProject ? remindersByProjectId.get(selectedProject.project_id) : undefined
  const showReminderCTA = selectedProject && isHighRisk(selectedProject.risk_level)

  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="logo-mark">ST</div>
            <div>
              <h1>Sprint Tracker</h1>
              <p>AI delivery-risk tracking for software teams</p>
            </div>
          </div>

          <nav className="side-nav">
            {NAV_ITEMS.map((item) => (
              <Button
                key={item.key}
                variant="ghost"
                size="md"
                className={`nav-button ${activeNav === item.key ? 'active' : ''}`}
                onClick={() => setActiveNav(item.key)}
              >
                {item.label}
              </Button>
            ))}
          </nav>

          <div className="sidebar-footer">
            <span className="coral-pill">Powered by Coral</span>
            <span className="muted-text">Sources connected: {connectedSources}</span>
          </div>
        </aside>

        <main className="main-content">
          <header className="topbar">
            <div>
              <h2>{NAV_ITEMS.find((item) => item.key === activeNav)?.label || 'Overview'}</h2>
              <p>Workspace: Oppia Demo</p>
            </div>
            <div className="topbar-meta">
              <span className={`mode-pill ${mode.toLowerCase().replace('_', '-')}`}>{modeLabel}</span>
              <span className="meta-pill">Last synced: {lastSynced || '-'}</span>
              <Button variant="secondary" onClick={() => void handleRefresh()} loading={refreshing}>
                Refresh
              </Button>
            </div>
          </header>

          {loading && (
            <section className="page-section">
              <article className="card loading-card">
                <div className="loading-line" />
                <div className="loading-line short" />
                <div className="loading-grid">
                  <div className="loading-box" />
                  <div className="loading-box" />
                  <div className="loading-box" />
                  <div className="loading-box" />
                </div>
              </article>
            </section>
          )}

          {!loading && error && (
            <section className="page-section">
              <article className="card error-card">
                <h3>Unable to load dashboard data</h3>
                <p>{error}</p>
                <Button variant="danger" onClick={() => void loadDashboardData()}>
                  Retry
                </Button>
              </article>
            </section>
          )}

          {!loading && !error && renderPage()}
        </main>

        {selectedLead && (
          <div className="overlay" onClick={() => setSelectedLead(null)}>
            <aside className="drawer" onClick={(e) => e.stopPropagation()}>
              <div className="drawer-head">
                <h3>{selectedLead.owner_lead}</h3>
                <Button variant="secondary" size="sm" onClick={() => setSelectedLead(null)}>
                  Close
                </Button>
              </div>

              <section className="drawer-section">
                <h4>Owner summary</h4>
                <p>Total projects: {selectedLead.total_projects_owned}</p>
                <p>High-risk projects: {selectedLead.high_risk_projects}</p>
                <p>Generated reminders: {selectedLead.generated_reminder_count}</p>
                <p>Contributors needing follow-up: {(selectedLead.contributors_needing_follow_up || []).join(', ') || '-'}</p>
                <p>Highest-risk project: {selectedLead.highest_risk_project || '-'}</p>
              </section>

              <section className="drawer-section">
                <h4>Projects under this lead</h4>
                {selectedLeadProjects.length === 0 && <p className="empty-state">No projects available.</p>}
                {selectedLeadProjects.length > 0 && (
                  <div className="table-wrap">
                    <table className="data-table compact">
                      <thead>
                        <tr>
                          <th>Project</th>
                          <th>Contributor</th>
                          <th>Risk</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedLeadProjects.map((project) => (
                          <tr key={project.project_id}>
                            <td>{project.project_name}</td>
                            <td>{project.project_owner_contributor || '-'}</td>
                            <td>
                              <span className={riskClass(project.risk_level)}>{project.risk_level}</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </aside>
          </div>
        )}

        {selectedProject && (
          <div className="overlay" onClick={() => setSelectedProject(null)}>
            <aside className="drawer project-drawer" onClick={(e) => e.stopPropagation()}>
              <div className="drawer-head">
                <h3>{selectedProject.project_name}</h3>
                <Button variant="secondary" size="sm" onClick={() => setSelectedProject(null)}>
                  Close
                </Button>
              </div>

              <section className="drawer-grid">
                <article className="drawer-card">
                  <h4>Project summary</h4>
                  <p>{selectedProject.project_description || 'No summary provided.'}</p>
                </article>
                <article className="drawer-card">
                  <h4>Risk score</h4>
                  <p className="score-display">{selectedProject.risk_score}</p>
                  <span className={riskClass(selectedProject.risk_level)}>{selectedProject.risk_level}</span>
                </article>
                <article className="drawer-card">
                  <h4>Owners</h4>
                  <p>Owner lead: {selectedProject.project_owner_lead || '-'}</p>
                  <p>Contributor: {selectedProject.project_owner_contributor || '-'}</p>
                </article>
                <article className="drawer-card">
                  <h4>Schedule and status</h4>
                  <p>Status: {selectedProject.project_status || '-'}</p>
                  <p>Planned date: {prettyDate(selectedProject.planned_completion_date)}</p>
                  <p>
                    Subtasks: {selectedProject.completed_subtasks}/{selectedProject.total_subtasks}
                  </p>
                </article>
              </section>

              <section className="drawer-section">
                <h4>Subtasks</h4>
                {selectedProject.subtasks.length === 0 && <p className="empty-state">No subtasks available.</p>}
                {selectedProject.subtasks.length > 0 && (
                  <div className="table-wrap">
                    <table className="data-table compact">
                      <thead>
                        <tr>
                          <th>Subtask</th>
                          <th>Status</th>
                          <th>Assignee</th>
                          <th>Estimated date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedProject.subtasks.map((subtask, idx) => (
                          <tr key={`${selectedProject.project_id}-sub-${idx}`}>
                            <td>{subtask.subtask || '-'}</td>
                            <td>{subtask.status || '-'}</td>
                            <td>{subtask.assignee || '-'}</td>
                            <td>{prettyDate(subtask.estimated_completion_date)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="drawer-section">
                <h4>Linked GitHub issues</h4>
                {(selectedProject.github_issue_evidence || []).length > 0 ? (
                  <ul className="evidence-list">
                    {selectedProject.github_issue_evidence.map((issue, idx) => (
                      <li key={`issue-${idx}`}>
                        #{issue.number}: {issue.title || issue.state || 'Issue evidence'}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>{(selectedProject.all_github_issue_numbers || []).join(', ') || 'No linked issues'}</p>
                )}
              </section>

              <section className="drawer-section">
                <h4>Linked GitHub PRs</h4>
                {(selectedProject.github_pr_evidence || []).length > 0 ? (
                  <ul className="evidence-list">
                    {selectedProject.github_pr_evidence.map((pr, idx) => (
                      <li key={`pr-${idx}`}>
                        #{pr.number}: {pr.title || pr.state || 'PR evidence'}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>{(selectedProject.all_github_pr_numbers || []).join(', ') || 'No linked PRs'}</p>
                )}
              </section>

              <section className="drawer-section">
                <h4>Risk drivers</h4>
                <ul className="evidence-list">
                  {(selectedProject.risk_drivers || []).map((driver, idx) => (
                    <li key={`risk-${idx}`}>{driver}</li>
                  ))}
                </ul>
              </section>

              <section className="drawer-section">
                <h4>Recommended actions</h4>
                <ul className="evidence-list">
                  {(selectedProject.recommendations || []).map((rec, idx) => (
                    <li key={`rec-${idx}`}>{rec}</li>
                  ))}
                </ul>
              </section>

              <section className="drawer-section">
                <h4>Reminder</h4>
                {showReminderCTA && (
                  <>
                    <p className="muted-text">Follow-up recommended.</p>
                    <Button
                      loading={generatingProjectId === selectedProject.project_id}
                      onClick={() => void generateReminderForProject(selectedProject)}
                    >
                      {projectReminder ? 'Regenerate reminder' : 'Generate reminder'}
                    </Button>
                    {projectReminder && (
                      <div className="reminder-preview">
                        <pre>{projectReminder.google_chat_text}</pre>
                        <div className="action-row">
                          <Button
                            variant={
                              copiedReminderIds.has(`drawer-${projectReminder.project_id}`) ? 'success' : 'secondary'
                            }
                            size="sm"
                            onClick={() =>
                              void copyText(`drawer-${projectReminder.project_id}`, projectReminder.google_chat_text)
                            }
                          >
                            {copiedReminderIds.has(`drawer-${projectReminder.project_id}`)
                              ? 'Copied'
                              : 'Copy Google Chat message'}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={!resolveReminderEmail(projectReminder).canEmail}
                            title={
                              resolveReminderEmail(projectReminder).canEmail
                                ? 'Open email draft'
                                : 'Contributor email unavailable'
                            }
                            onClick={() => openEmailDraft(projectReminder)}
                          >
                            Open email draft
                          </Button>
                        </div>
                        {!resolveReminderEmail(projectReminder).canEmail && (
                          <p className="muted-text small-text">Contributor email unavailable</p>
                        )}
                      </div>
                    )}
                  </>
                )}
                {!showReminderCTA && <p>No reminder needed — this project appears on track.</p>}
              </section>

              <section className="drawer-section">
                <details>
                  <summary>Technical evidence</summary>
                  <div className="technical-block">
                    <p>Planning source: Google Sheet synced via Coral.</p>
                    <p>Engineering source: GitHub issues and pull requests via Coral.</p>
                    <p>Workspace context: Oppia demo workspace.</p>
                    <h5>Query flow summary</h5>
                    <ol>
                      {(selectedProject.coral_query_flow_used?.steps || []).map((step, idx) => (
                        <li key={`step-${idx}`}>{step}</li>
                      ))}
                    </ol>
                    {(selectedProject.coral_query_flow_used?.queries || []).length > 0 && (
                      <>
                        <h5>Query evidence</h5>
                        <pre>{(selectedProject.coral_query_flow_used?.queries || []).join('\n')}</pre>
                      </>
                    )}
                  </div>
                </details>
              </section>
            </aside>
          </div>
        )}
      </div>

      <Toast toasts={toasts} onDismiss={dismissToast} />
    </>
  )
}
