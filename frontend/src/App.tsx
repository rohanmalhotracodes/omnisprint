import React, { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  agentQuery,
  fetchLatestActivity,
  fetchHealth,
  fetchHighRiskReminders,
  fetchOwners,
  fetchProjects,
  generateReminders,
  syncRoadmap,
  type AgentAskResponse,
  type HealthResponse,
  type LeadOwnerRisk,
  type LatestActivityResponse,
  type OwnersResponse,
  type ProjectReport,
  type Reminder,
} from './api'
import Button from './components/Button'
import Toast, { type ToastItem, type ToastTone } from './components/Toast'
import StatCard from './components/StatCard'
import RiskBadge from './components/RiskBadge'
import RiskDistribution from './components/RiskDistribution'
import OwnerRiskChart from './components/OwnerRiskChart'
import EmptyState from './components/EmptyState'
import LoadingState from './components/LoadingState'

type NavKey = 'overview' | 'latest_activity' | 'projects' | 'owners' | 'actions' | 'assistant'

type ChatMessage = {
  role: 'user' | 'assistant'
  text: string
  response?: AgentAskResponse
}

type IssueLink = {
  number: number
  title?: string
  state?: string
  updatedAt?: string
  htmlUrl: string
}

type PrLink = {
  number: number
  title?: string
  state?: string
  draft?: boolean | null
  updatedAt?: string
  htmlUrl: string
}

const NAV_ITEMS: Array<{ key: NavKey; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'latest_activity', label: 'Latest Activity' },
  { key: 'projects', label: 'Projects' },
  { key: 'owners', label: 'Owners' },
  { key: 'actions', label: 'Actions' },
  { key: 'assistant', label: 'Omni' },
]

const SUGGESTED_PROMPTS = [
  'Which projects need attention today?',
  'Catch me up on latest activity.',
  'Which owner needs follow-up?',
  'Which PR is blocking the highest-risk project?',
  'Which commit may have caused regression?',
  'Draft an email for the riskiest project.',
  'Generate Google Chat reminders for high-risk projects.',
  'Show technical evidence for Coral retrieval.',
]

const CORE_CACHE_KEY = 'omnisprint_core_cache_v1'
const CORE_CACHE_TTL_MS = 15 * 60 * 1000

function parseDateValue(value?: string | null): Date | null {
  if (!value) return null
  const raw = String(value).trim()
  const hasExplicitYear = /\b(19|20)\d{2}\b/.test(raw)
  const currentYear = new Date().getFullYear()

  if (!hasExplicitYear) {
    const withYearFirst = new Date(`${raw} ${currentYear}`)
    if (!Number.isNaN(withYearFirst.getTime())) return withYearFirst
  }

  const direct = new Date(raw)
  if (!Number.isNaN(direct.getTime())) return direct

  const withYear = new Date(`${raw} ${currentYear}`)
  if (!Number.isNaN(withYear.getTime())) return withYear
  return null
}

function prettyDate(value?: string | null): string {
  const dt = parseDateValue(value)
  if (!dt) return value || '-'
  return dt.toLocaleDateString()
}

function prettyDateTime(value?: string | null): string {
  const dt = parseDateValue(value)
  if (!dt) return value || '-'
  return dt.toLocaleString()
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

  const subject = encodeURIComponent(`Quick check-in on ${reminder.project_name}`)
  const body = encodeURIComponent(reminder.google_chat_text || '')
  const mailtoUrl = `mailto:${explicitEmail}?subject=${subject}&body=${body}`
  return { canEmail: true, mailtoUrl }
}

function normalizeAgentReminder(reminder: any): Reminder | null {
  if (!reminder || typeof reminder !== 'object') return null
  const projectId = String(reminder.project_id || '').trim()
  const projectName = String(reminder.project_name || '').trim()
  if (!projectId || !projectName) return null
  return {
    project_id: projectId,
    project_name: projectName,
    project_owner_lead: reminder.project_owner_lead || reminder.lead || '',
    project_owner_contributor: reminder.project_owner_contributor || reminder.contributor || '',
    risk_level: (reminder.risk_level || 'HIGH') as Reminder['risk_level'],
    risk_score: Number(reminder.risk_score || 0),
    reason: String(reminder.reason || ''),
    google_chat_text: String(reminder.google_chat_text || ''),
    risk_drivers: Array.isArray(reminder.risk_drivers) ? reminder.risk_drivers : [],
    can_email: Boolean(reminder.can_email || reminder.mailto_url || reminder.contributor_email),
    mailto_url: reminder.mailto_url || undefined,
    contributor_email: reminder.contributor_email || undefined,
  }
}

function repoSlug(health: HealthResponse | null): string {
  return (health?.target_repo || 'oppia/oppia').trim() || 'oppia/oppia'
}

function fallbackIssueUrl(slug: string, number: number): string {
  return `https://github.com/${slug}/issues/${number}`
}

function fallbackPrUrl(slug: string, number: number): string {
  return `https://github.com/${slug}/pull/${number}`
}

function reminderCardId(projectId: string): string {
  const safeProjectId = String(projectId || '').replace(/[^a-zA-Z0-9_-]/g, '_')
  return `reminder-card-${safeProjectId}`
}

function renderInlineMarkdown(text: string, keyPrefix: string): React.ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g
  const nodes: React.ReactNode[] = []
  let last = 0
  let index = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    const start = match.index
    const token = match[0]
    if (start > last) {
      nodes.push(text.slice(last, start))
    }

    if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(<strong key={`${keyPrefix}-b-${index++}`}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(<code key={`${keyPrefix}-c-${index++}`}>{token.slice(1, -1)}</code>)
    } else {
      const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/)
      if (link) {
        nodes.push(
          <a key={`${keyPrefix}-a-${index++}`} href={link[2]} target="_blank" rel="noreferrer">
            {link[1]}
          </a>,
        )
      } else {
        nodes.push(token)
      }
    }

    last = pattern.lastIndex
  }

  if (last < text.length) {
    nodes.push(text.slice(last))
  }
  return nodes
}

function renderMarkdownText(text: string): React.ReactNode {
  const lines = String(text || '').replace(/\r/g, '').split('\n')
  const nodes: React.ReactNode[] = []
  let unordered: string[] = []
  let ordered: string[] = []
  let paragraphIndex = 0

  const flushUnordered = () => {
    if (unordered.length === 0) return
    const key = `ul-${nodes.length}`
    nodes.push(
      <ul key={key}>
        {unordered.map((item, idx) => (
          <li key={`${key}-${idx}`}>{renderInlineMarkdown(item, `${key}-${idx}`)}</li>
        ))}
      </ul>,
    )
    unordered = []
  }

  const flushOrdered = () => {
    if (ordered.length === 0) return
    const key = `ol-${nodes.length}`
    nodes.push(
      <ol key={key}>
        {ordered.map((item, idx) => (
          <li key={`${key}-${idx}`}>{renderInlineMarkdown(item, `${key}-${idx}`)}</li>
        ))}
      </ol>,
    )
    ordered = []
  }

  for (const rawLine of lines) {
    const bullet = rawLine.match(/^\s*[-*]\s+(.+)$/)
    if (bullet) {
      flushOrdered()
      unordered.push(bullet[1])
      continue
    }

    const numbered = rawLine.match(/^\s*\d+\.\s+(.+)$/)
    if (numbered) {
      flushUnordered()
      ordered.push(numbered[1])
      continue
    }

    if (!rawLine.trim()) {
      flushUnordered()
      flushOrdered()
      continue
    }

    flushUnordered()
    flushOrdered()
    const key = `p-${paragraphIndex++}`
    nodes.push(
      <p key={key} className="chat-message-paragraph">
        {renderInlineMarkdown(rawLine, key)}
      </p>,
    )
  }

  flushUnordered()
  flushOrdered()
  return nodes
}

function buildIssueLinks(project: ProjectReport, slug: string): IssueLink[] {
  const map = new Map<number, IssueLink>()
  for (const issue of project.github_issue_evidence || []) {
    const num = Number(issue.number)
    if (!num) continue
    map.set(num, {
      number: num,
      title: issue.title,
      state: issue.state,
      updatedAt: issue.updated_at,
      htmlUrl: issue.html_url || fallbackIssueUrl(slug, num),
    })
  }

  for (const rawNum of project.all_github_issue_numbers || []) {
    const num = Number(rawNum)
    if (!num || map.has(num)) continue
    map.set(num, {
      number: num,
      title: '',
      state: 'unknown',
      updatedAt: '',
      htmlUrl: fallbackIssueUrl(slug, num),
    })
  }

  return Array.from(map.values()).sort((a, b) => a.number - b.number)
}

function buildPrLinks(project: ProjectReport, slug: string): PrLink[] {
  const map = new Map<number, PrLink>()
  for (const pr of project.github_pr_evidence || []) {
    const num = Number(pr.number)
    if (!num) continue
    map.set(num, {
      number: num,
      title: pr.title,
      state: pr.state,
      draft: pr.draft,
      updatedAt: pr.updated_at,
      htmlUrl: pr.html_url || fallbackPrUrl(slug, num),
    })
  }

  for (const rawNum of project.all_github_pr_numbers || []) {
    const num = Number(rawNum)
    if (!num || map.has(num)) continue
    map.set(num, {
      number: num,
      title: '',
      state: 'unknown',
      draft: null,
      updatedAt: '',
      htmlUrl: fallbackPrUrl(slug, num),
    })
  }

  return Array.from(map.values()).sort((a, b) => a.number - b.number)
}

function projectNeedsReminder(project: ProjectReport): boolean {
  // Keep overview and Actions queue consistent:
  // reminders are generated only for HIGH/CRITICAL projects by default.
  return isHighRisk(project.risk_level)
}

export default function App() {
  const [activeNav, setActiveNav] = useState<NavKey>('overview')

  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [projects, setProjects] = useState<ProjectReport[]>([])
  const [owners, setOwners] = useState<OwnersResponse>({ leads: [], contributors: [] })
  const [reminders, setReminders] = useState<Reminder[]>([])

  const [coreLoading, setCoreLoading] = useState(true)
  const [coreRefreshing, setCoreRefreshing] = useState(false)
  const [coreError, setCoreError] = useState<string | null>(null)
  const [latestActivity, setLatestActivity] = useState<LatestActivityResponse | null>(null)
  const [activityLoading, setActivityLoading] = useState(false)
  const [activityLoaded, setActivityLoaded] = useState(false)
  const [activityError, setActivityError] = useState<string | null>(null)

  const [ownersLoading, setOwnersLoading] = useState(false)
  const [ownersLoaded, setOwnersLoaded] = useState(false)
  const [ownersError, setOwnersError] = useState<string | null>(null)

  const [remindersLoading, setRemindersLoading] = useState(false)
  const [remindersLoaded, setRemindersLoaded] = useState(false)
  const [remindersError, setRemindersError] = useState<string | null>(null)

  const [selectedProject, setSelectedProject] = useState<ProjectReport | null>(null)
  const [selectedLead, setSelectedLead] = useState<LeadOwnerRisk | null>(null)

  const [lastSynced, setLastSynced] = useState<string>(formatSyncTime(new Date().toISOString()))

  const [projectSearch, setProjectSearch] = useState('')
  const [projectRiskFilter, setProjectRiskFilter] = useState<'ALL' | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'>('ALL')
  const [projectOwnerFilter, setProjectOwnerFilter] = useState('ALL')
  const [projectSort, setProjectSort] = useState<'risk_desc' | 'risk_asc'>('risk_desc')

  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [actionsFocusProjectId, setActionsFocusProjectId] = useState<string | null>(null)

  const [generatingProjectId, setGeneratingProjectId] = useState<string | null>(null)
  const [copiedReminderIds, setCopiedReminderIds] = useState<Set<string>>(new Set())

  const [toasts, setToasts] = useState<ToastItem[]>([])

  const toastIdRef = useRef(1)
  const copyTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const assistantChatLogRef = useRef<HTMLDivElement | null>(null)
  const coreInFlightRef = useRef(false)
  const ownersInFlightRef = useRef(false)
  const remindersInFlightRef = useRef(false)
  const activityInFlightRef = useRef(false)

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

  useEffect(() => {
    if (activeNav !== 'assistant') return
    const el = assistantChatLogRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [activeNav, chatMessages, chatLoading])

  const markReminderCopied = useCallback((key: string) => {
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
  }, [])

  const mapCoreError = useCallback((message: string) => {
    const lowered = message.toLowerCase()
    if (lowered.includes('timed out')) {
      return 'Live data query timed out. Coral/source retrieval is too slow right now.'
    }
    if (lowered.includes('network error') || lowered.includes('failed to fetch')) {
      return 'Backend is not reachable right now. Start omnisprint services and retry.'
    }
    return message
  }, [])

  const loadCoreData = useCallback(
    async (silent = false) => {
      if (coreInFlightRef.current) return
      coreInFlightRef.current = true

      if (silent) {
        if (projects.length > 0) {
          setCoreRefreshing(true)
        }
      } else {
        setCoreLoading(true)
      }

      setCoreError(null)

      try {
        const [healthResult, projectsResult] = await Promise.allSettled([fetchHealth(), fetchProjects()])
        const nextHealth = healthResult.status === 'fulfilled' ? healthResult.value : null

        if (healthResult.status === 'fulfilled') {
          setHealth(healthResult.value)
        } else {
          pushToast('Health status is delayed. Retrying on next refresh.', 'info')
        }

        if (projectsResult.status === 'rejected') {
          const detail = projectsResult.reason instanceof Error ? projectsResult.reason.message : 'Request failed'
          const message = mapCoreError(detail)
          setCoreError(message)
          if (!silent) {
            setProjects([])
            setOwners({ leads: [], contributors: [] })
            setOwnersLoaded(false)
            setReminders([])
            setRemindersLoaded(false)
          }
          pushToast('Failed to load projects. Please try again.', 'error', 3200)
          return
        }

        setProjects(projectsResult.value || [])
        setLastSynced(formatSyncTime(new Date().toISOString()))
        try {
          const payload = {
            cachedAt: new Date().toISOString(),
            health: nextHealth,
            projects: projectsResult.value || [],
          }
          window.sessionStorage.setItem(CORE_CACHE_KEY, JSON.stringify(payload))
        } catch {
          // Best-effort client cache for faster perceived loads.
        }
      } finally {
        setCoreLoading(false)
        setCoreRefreshing(false)
        coreInFlightRef.current = false
      }
    },
    [mapCoreError, projects.length, pushToast],
  )

  const loadOwners = useCallback(
    async (force = false) => {
      if (ownersInFlightRef.current) return
      if (!force && ownersLoaded) return
      ownersInFlightRef.current = true
      setOwnersLoading(true)
      setOwnersError(null)

      try {
        const result = await fetchOwners()
        setOwners(result || { leads: [], contributors: [] })
        setOwnersLoaded(true)
      } catch (err: any) {
        const message = err?.message || 'Failed to load owner data.'
        setOwnersError(message)
        pushToast('Failed to load owner risk data', 'error', 3200)
      } finally {
        setOwnersLoading(false)
        ownersInFlightRef.current = false
      }
    },
    [ownersLoaded, pushToast],
  )

  const loadReminders = useCallback(
    async (force = false) => {
      if (remindersInFlightRef.current) return
      if (!force && remindersLoaded) return
      remindersInFlightRef.current = true
      setRemindersLoading(true)
      setRemindersError(null)

      try {
        const result = await fetchHighRiskReminders()
        setReminders(result?.reminders || [])
        setRemindersLoaded(true)
      } catch (err: any) {
        const message = err?.message || 'Failed to load reminders.'
        setRemindersError(message)
        pushToast('Failed to load reminders', 'error', 3200)
      } finally {
        setRemindersLoading(false)
        remindersInFlightRef.current = false
      }
    },
    [remindersLoaded, pushToast],
  )

  const loadLatestActivity = useCallback(
    async (force = false) => {
      if (activityInFlightRef.current) return
      if (!force && activityLoaded) return
      activityInFlightRef.current = true
      setActivityLoading(true)
      setActivityError(null)
      try {
        const result = await fetchLatestActivity(8)
        setLatestActivity(result)
        setActivityLoaded(true)
      } catch (err: any) {
        const message = err?.message || 'Failed to load latest activity.'
        setActivityError(message)
      } finally {
        setActivityLoading(false)
        activityInFlightRef.current = false
      }
    },
    [activityLoaded],
  )

  useEffect(() => {
    let hydrated = false
    try {
      const raw = window.sessionStorage.getItem(CORE_CACHE_KEY)
      if (raw) {
        const cached = JSON.parse(raw)
        const cachedAtMs = Date.parse(String(cached?.cachedAt || ''))
        const ageMs = Number.isNaN(cachedAtMs) ? Infinity : Date.now() - cachedAtMs
        if (ageMs <= CORE_CACHE_TTL_MS && Array.isArray(cached?.projects)) {
          if (cached.health && typeof cached.health === 'object') {
            setHealth(cached.health)
          }
          setProjects(cached.projects)
          setLastSynced(formatSyncTime(cached.cachedAt))
          setCoreLoading(false)
          hydrated = true
        }
      }
    } catch {
      // Ignore cache parse errors and continue with live load.
    }

    void loadCoreData(hydrated)
  }, [loadCoreData])

  useEffect(() => {
    if (coreLoading) return
    if (activeNav === 'owners') {
      void loadOwners(false)
    } else if (activeNav === 'actions') {
      void loadReminders(false)
    }
  }, [activeNav, coreLoading, loadOwners, loadReminders])

  useEffect(() => {
    if (coreLoading || coreError) return
    if (activeNav !== 'latest_activity') return
    const timer = window.setTimeout(() => {
      void loadLatestActivity(false)
    }, 180)
    return () => window.clearTimeout(timer)
  }, [activeNav, coreError, coreLoading, loadLatestActivity])

  useEffect(() => {
    if (activeNav !== 'actions') return
    if (!actionsFocusProjectId) return
    if (remindersLoading) return

    const hasReminder = reminders.some((reminder) => reminder.project_id === actionsFocusProjectId)
    if (!hasReminder) return

    const targetNode = document.getElementById(reminderCardId(actionsFocusProjectId))
    if (!targetNode) return

    targetNode.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const timer = window.setTimeout(() => {
      setActionsFocusProjectId(null)
    }, 1600)
    return () => window.clearTimeout(timer)
  }, [activeNav, actionsFocusProjectId, reminders, remindersLoading])

  const handleRefresh = useCallback(async () => {
    if (coreRefreshing) return

    try {
      const syncResult = await syncRoadmap()
      if (syncResult?.status) {
        pushToast(`Refresh complete (${syncResult.status})`, 'success')
      } else {
        pushToast('Refresh complete', 'success')
      }
    } catch (err: any) {
      pushToast(err?.message || 'Refresh failed; reloading current data.', 'info')
    }

    await loadCoreData(true)

    if (activeNav === 'owners' && ownersLoaded) {
      await loadOwners(true)
    }
    if (activeNav === 'actions' && remindersLoaded) {
      await loadReminders(true)
    }
    if (activeNav === 'overview' && activityLoaded) {
      await loadLatestActivity(true)
    }
  }, [
    activeNav,
    activityLoaded,
    coreRefreshing,
    loadCoreData,
    loadLatestActivity,
    loadOwners,
    loadReminders,
    ownersLoaded,
    remindersLoaded,
    pushToast,
  ])

  const slug = repoSlug(health)

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
        const haystack = [p.project_name, p.project_owner_lead, p.project_owner_contributor, p.project_status]
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
  }, [projectOwnerFilter, projectRiskFilter, projectSearch, projectSort, projects])

  const topRisks = useMemo(
    () => [...projects].sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0)).slice(0, 5),
    [projects],
  )

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

  const overviewMetrics = useMemo(() => {
    const totalProjects = projects.length
    const needsAttention = projects.filter((p) => isHighRisk(p.risk_level)).length
    const blockedProjects = projects.filter(
      (p) => (p.blocked_subtasks || 0) > 0 || String(p.project_status || '').toLowerCase().includes('block'),
    ).length
    const remindersReady = projects.filter((p) => projectNeedsReminder(p)).length

    return {
      totalProjects,
      needsAttention,
      blockedProjects,
      remindersReady,
    }
  }, [projects])

  const remindersByLead = useMemo(() => {
    const grouped: Record<string, Reminder[]> = {}
    for (const reminder of reminders) {
      const key = reminder.project_owner_lead || '(unassigned lead)'
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(reminder)
    }
    return Object.entries(grouped).sort((a, b) => a[0].localeCompare(b[0]))
  }, [reminders])

  const remindersByProjectId = useMemo(() => {
    const index = new Map<string, Reminder>()
    for (const reminder of reminders) {
      index.set(reminder.project_id, reminder)
    }
    return index
  }, [reminders])

  const ownerRiskChartItems = useMemo(() => {
    return [...owners.leads]
      .map((lead) => ({
        owner: lead.owner_lead,
        highRiskProjects: lead.high_risk_projects || 0,
      }))
      .sort((a, b) => b.highRiskProjects - a.highRiskProjects)
      .slice(0, 5)
  }, [owners.leads])

  const selectedLeadProjects = useMemo(() => {
    if (!selectedLead) return []
    return projects
      .filter((p) => (p.project_owner_lead || '(unassigned lead)') === selectedLead.owner_lead)
      .sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0))
  }, [projects, selectedLead])

  const issueLinksByProjectId = useMemo(() => {
    const map = new Map<string, IssueLink[]>()
    for (const project of projects) {
      map.set(project.project_id, buildIssueLinks(project, slug))
    }
    return map
  }, [projects, slug])

  const prLinksByProjectId = useMemo(() => {
    const map = new Map<string, PrLink[]>()
    for (const project of projects) {
      map.set(project.project_id, buildPrLinks(project, slug))
    }
    return map
  }, [projects, slug])

  const copyText = useCallback(
    async (key: string, text: string) => {
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        window.prompt('Copy message:', text)
      }
      markReminderCopied(key)
      pushToast('Message copied', 'success')
    },
    [markReminderCopied, pushToast],
  )

  const copyPlainText = useCallback(
    async (text: string, successMessage: string) => {
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        window.prompt('Copy text:', text)
      }
      pushToast(successMessage, 'success')
    },
    [pushToast],
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

  const openMailtoUrl = useCallback(
    (mailtoUrl?: string | null) => {
      if (!mailtoUrl) {
        pushToast('Email draft URL unavailable', 'info')
        return
      }
      window.open(mailtoUrl, '_blank')
      pushToast('Email draft opened', 'success')
    },
    [pushToast],
  )

  const refreshActionQueue = useCallback(async () => {
    if (remindersLoading) return
    setRemindersLoading(true)
    setRemindersError(null)
    try {
      const result = await generateReminders({ risk_threshold: 'HIGH' })
      const next = result?.reminders || []
      setReminders(next)
      setRemindersLoaded(true)
      if (next.length === 0) {
        pushToast('No high-risk reminders needed right now.', 'info')
      } else {
        pushToast(`Generated ${next.length} targeted reminder${next.length === 1 ? '' : 's'}`, 'success')
      }
    } catch (err: any) {
      const message = err?.message || 'Failed to generate reminders.'
      setRemindersError(message)
      pushToast('Failed to generate reminders', 'error', 3200)
    } finally {
      setRemindersLoading(false)
    }
  }, [pushToast, remindersLoading])

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
        setRemindersLoaded(true)

        pushToast('Reminder generated', 'success')
      } catch (err: any) {
        pushToast(err?.message || 'Failed to generate reminder', 'error', 3200)
      } finally {
        setGeneratingProjectId(null)
      }
    },
    [generatingProjectId, pushToast],
  )

  const openActionsQueueForProject = useCallback((projectId?: string | null) => {
    const id = String(projectId || '').trim()
    setSelectedProject(null)
    setActiveNav('actions')
    if (id) {
      setActionsFocusProjectId(id)
    }
  }, [])

  const sendAssistantQuestion = useCallback(
    async (question: string) => {
      const q = question.trim()
      if (!q || chatLoading) return

      setChatMessages((prev) => [...prev, { role: 'user', text: q }])
      setChatInput('')
      setChatLoading(true)

      try {
        const result = await agentQuery(q)
        if (result?.fallback_used) {
          const reason = String(result?.fallback_reason || '').trim()
          const brief = reason ? reason.slice(0, 140) : 'Reason unavailable'
          pushToast(`Omni fallback mode: ${brief}`, 'info', 4200)
        }
        setChatMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            text: result?.answer || 'No response.',
            response: result,
          },
        ])
      } catch (err: any) {
        setChatMessages((prev) => [
          ...prev,
          { role: 'assistant', text: `Unable to get Omni response: ${err?.message || 'Unknown error'}` },
        ])
      } finally {
        setChatLoading(false)
      }
    },
    [chatLoading, pushToast],
  )

  const onChatSubmit = (e: FormEvent) => {
    e.preventDefault()
    void sendAssistantQuestion(chatInput)
  }

  const onChatInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    e.preventDefault()
    if (!chatLoading && chatInput.trim()) {
      void sendAssistantQuestion(chatInput)
    }
  }

  const renderIssueChips = useCallback(
    (project: ProjectReport, max = 3) => {
      const issues = issueLinksByProjectId.get(project.project_id) || []
      if (issues.length === 0) return <span className="muted-text">-</span>

      return (
        <div className="chip-row">
          {issues.slice(0, max).map((issue) => (
            <a
              key={`issue-chip-${project.project_id}-${issue.number}`}
              className="evidence-chip"
              href={issue.htmlUrl}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              #{issue.number}
            </a>
          ))}
          {issues.length > max && <span className="chip-more">+{issues.length - max}</span>}
        </div>
      )
    },
    [issueLinksByProjectId],
  )

  const renderPrChips = useCallback(
    (project: ProjectReport, max = 3) => {
      const prs = prLinksByProjectId.get(project.project_id) || []
      if (prs.length === 0) return <span className="muted-text">-</span>

      return (
        <div className="chip-row">
          {prs.slice(0, max).map((pr) => (
            <a
              key={`pr-chip-${project.project_id}-${pr.number}`}
              className="evidence-chip"
              href={pr.htmlUrl}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              #{pr.number}
            </a>
          ))}
          {prs.length > max && <span className="chip-more">+{prs.length - max}</span>}
        </div>
      )
    },
    [prLinksByProjectId],
  )

  const renderSubtaskRefChips = useCallback(
    (type: 'issue' | 'pr', numbers?: number[]) => {
      const unique = Array.from(new Set((numbers || []).map((n) => Number(n)).filter((n) => n > 0))).sort(
        (a, b) => a - b,
      )
      if (unique.length === 0) return <span className="muted-text">-</span>

      return (
        <div className="chip-row">
          {unique.map((num) => (
            <a
              key={`${type}-subtask-${num}`}
              className="evidence-chip"
              href={type === 'issue' ? fallbackIssueUrl(slug, num) : fallbackPrUrl(slug, num)}
              target="_blank"
              rel="noreferrer"
            >
              #{num}
            </a>
          ))}
        </div>
      )
    },
    [slug],
  )

  const renderOverview = () => (
    <section className="page-section">
      <div className="kpi-grid overview-kpi-grid">
        <StatCard label="Total Projects" value={overviewMetrics.totalProjects} />
        <StatCard label="Needs Attention" value={overviewMetrics.needsAttention} />
        <StatCard label="Blocked" value={overviewMetrics.blockedProjects} />
        <StatCard label="Reminders Ready" value={overviewMetrics.remindersReady} />
      </div>

      <div className="overview-main-grid">
        <article className="card page-card">
          <div className="card-head">
            <h3 className="section-title">Top 5 at-risk projects</h3>
            <Button variant="secondary" size="sm" onClick={() => setActiveNav('projects')}>
              View all projects
            </Button>
          </div>

          {topRisks.length === 0 && <EmptyState text="No projects found. Refresh planning data or check source status." />}

          {topRisks.length > 0 && (
            <div className="table-wrap">
              <table className="data-table compact">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Project Leads</th>
                    <th>Contributor</th>
                    <th>Risk</th>
                    <th>Linked evidence</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {topRisks.map((project) => (
                    <tr key={project.project_id} className="clickable-row" onClick={() => setSelectedProject(project)}>
                      <td>{project.project_name}</td>
                      <td>{project.project_owner_lead || '-'}</td>
                      <td>{project.project_owner_contributor || '-'}</td>
                      <td>
                        <div className="risk-stack">
                          <RiskBadge level={project.risk_level} />
                          <span className="risk-score">{project.risk_score}</span>
                        </div>
                      </td>
                      <td>
                        <div className="linked-evidence-stack">
                          <span>Issues: {renderIssueChips(project, 2)}</span>
                          <span>PRs: {renderPrChips(project, 2)}</span>
                        </div>
                      </td>
                      <td>
                        <div className="action-row">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={(e) => {
                              e.stopPropagation()
                              setSelectedProject(project)
                            }}
                          >
                            View details
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

        <div className="overview-side-grid">
          <RiskDistribution counts={riskDistribution} />
          <article className="card page-card">
            <h3 className="section-title">Recent action items</h3>
            <ul className="action-list">
              <li>{overviewMetrics.needsAttention} projects currently need attention.</li>
              <li>{overviewMetrics.blockedProjects} projects include blocked subtasks.</li>
              <li>{overviewMetrics.remindersReady} projects are candidates for targeted follow-up.</li>
              <li>
                {projects.reduce((sum, p) => sum + (p.open_linked_pr_count || 0), 0)} open linked PRs need review flow.
              </li>
            </ul>
            <div className="action-row section-subtitle">
              <Button onClick={() => setActiveNav('actions')}>
                Open actions queue
              </Button>
            </div>
          </article>
        </div>
      </div>

    </section>
  )

  const renderLatestActivity = () => {
    const prRows = ((latestActivity?.data?.latest_pull_requests || []) as any[]).slice(0, 5)
    const issueRows = ((latestActivity?.data?.latest_issues || []) as any[]).slice(0, 5)
    const commitRows = ((latestActivity?.data?.latest_commits || []) as any[]).slice(0, 5)
    const prTotal = (latestActivity?.data?.latest_pull_requests || []).length
    const issueTotal = (latestActivity?.data?.latest_issues || []).length
    const commitTotal = (latestActivity?.data?.latest_commits || []).length
    const prShown = prRows.length
    const issueShown = issueRows.length
    const commitShown = commitRows.length
    const prStatus = latestActivity?.data?.pulls_fallback_used
      ? 'Fallback'
      : latestActivity?.data?.pulls_status === 'success'
        ? 'Live'
        : 'Delayed'
    const issueStatus = latestActivity?.data?.issues_fallback_used
      ? 'Fallback'
      : latestActivity?.data?.issues_status === 'success'
        ? 'Live'
        : 'Delayed'
    const commitStatus = latestActivity?.data?.commits_status === 'success'
      ? 'Live'
      : latestActivity?.data?.commits_status === 'unavailable'
        ? 'Unavailable'
        : 'Delayed'

    return (
      <section className="page-section">
        <article className="card page-card">
          <p className="muted-text page-intro">Recently updated pull requests, issues, and commits.</p>

          {activityLoading && !activityLoaded && <LoadingState label="Loading latest activity..." />}
          {!activityLoading && !activityError && !activityLoaded && <LoadingState label="Loading latest activity..." />}
          {!activityLoading && activityError && (
            <div className="inline-error-row">
              <p className="muted-text">{activityError}</p>
              <Button variant="danger" size="sm" onClick={() => void loadLatestActivity(true)}>
                Retry
              </Button>
            </div>
          )}

          {!activityLoading && !activityError && activityLoaded && (
            <>
              <div className="activity-summary-row">
                <div className="activity-summary-card">
                  <span className="activity-summary-label">Pull requests shown</span>
                  <strong>{prShown}</strong>
                  {prTotal > prShown && <p className="muted-text small-text">of {prTotal} total</p>}
                </div>
                <div className="activity-summary-card">
                  <span className="activity-summary-label">Issues shown</span>
                  <strong>{issueShown}</strong>
                  {issueTotal > issueShown && <p className="muted-text small-text">of {issueTotal} total</p>}
                </div>
                <div className="activity-summary-card">
                  <span className="activity-summary-label">Commits shown</span>
                  <strong>{commitShown}</strong>
                  {commitTotal > commitShown && <p className="muted-text small-text">of {commitTotal} total</p>}
                </div>
              </div>

              {latestActivity?.data?.latest_pr_brief && (
                <div className="activity-brief">
                  <span className="activity-brief-label">Gemini brief</span>
                  <p>{latestActivity.data.latest_pr_brief}</p>
                </div>
              )}

              <div className="activity-grid">
                <section className="activity-panel">
                  <div className="activity-panel-head">
                    <h4 className="mini-title">Pull requests</h4>
                    {prStatus !== 'Live' && <span className={`activity-status-pill ${prStatus.toLowerCase()}`}>{prStatus}</span>}
                  </div>
                  {latestActivity?.data?.pulls_fallback_used && (
                    <p className="muted-text small-text activity-note">
                      Live PR activity unavailable. Showing linked project evidence.
                    </p>
                  )}
                  {!latestActivity?.data?.pulls_fallback_used &&
                    latestActivity?.data?.pulls_status &&
                    latestActivity.data.pulls_status !== 'success' && (
                      <p className="muted-text small-text activity-note">
                        {latestActivity.data.pulls_summary || 'Pull request activity is temporarily unavailable.'}
                      </p>
                    )}
                  <ul className="compact-link-list">
                    {prRows.map((pr) => (
                      <li key={`ov-pr-${pr.number || pr.html_url}`}>
                        <a href={pr.html_url || fallbackPrUrl(slug, Number(pr.number || 0))} target="_blank" rel="noreferrer">
                          #{pr.number} {pr.title || 'Untitled PR'}
                        </a>
                      </li>
                    ))}
                    {prRows.length === 0 && <li className="muted-text">No pull request updates available.</li>}
                  </ul>
                </section>

                <section className="activity-panel">
                  <div className="activity-panel-head">
                    <h4 className="mini-title">Issues</h4>
                    {issueStatus !== 'Live' && (
                      <span className={`activity-status-pill ${issueStatus.toLowerCase()}`}>{issueStatus}</span>
                    )}
                  </div>
                  {latestActivity?.data?.issues_fallback_used && (
                    <p className="muted-text small-text activity-note">
                      Live issue activity unavailable. Showing linked project evidence.
                    </p>
                  )}
                  {!latestActivity?.data?.issues_fallback_used &&
                    latestActivity?.data?.issues_status &&
                    latestActivity.data.issues_status !== 'success' && (
                      <p className="muted-text small-text activity-note">
                        {latestActivity.data.issues_summary || 'Issue activity is temporarily unavailable.'}
                      </p>
                    )}
                  <ul className="compact-link-list">
                    {issueRows.map((issue) => (
                      <li key={`ov-issue-${issue.number || issue.html_url}`}>
                        <a
                          href={issue.html_url || fallbackIssueUrl(slug, Number(issue.number || 0))}
                          target="_blank"
                          rel="noreferrer"
                        >
                          #{issue.number} {issue.title || 'Untitled issue'}
                        </a>
                      </li>
                    ))}
                    {issueRows.length === 0 && <li className="muted-text">No issue updates available.</li>}
                  </ul>
                </section>

                <section className="activity-panel">
                  <div className="activity-panel-head">
                    <h4 className="mini-title">Commits</h4>
                    {commitStatus !== 'Live' && (
                      <span className={`activity-status-pill ${commitStatus.toLowerCase()}`}>{commitStatus}</span>
                    )}
                  </div>
                  {latestActivity?.data?.commits_status && latestActivity.data.commits_status !== 'success' && (
                    <p className="muted-text small-text activity-note">
                      {latestActivity.data.commits_summary ||
                        (latestActivity?.data?.commits_status === 'unavailable'
                          ? 'Commit feed is not available from connected sources.'
                          : 'Commit activity is temporarily unavailable.')}
                    </p>
                  )}
                  <ul className="compact-link-list">
                    {commitRows.map((commit) => (
                      <li key={`ov-commit-${commit.sha || commit.html_url}`}>
                        {commit.html_url ? (
                          <a href={commit.html_url} target="_blank" rel="noreferrer">
                            {String(commit.sha || '').slice(0, 8) || 'commit'} {commit.message || ''}
                          </a>
                        ) : (
                          <span>
                            {String(commit.sha || '').slice(0, 8) || 'commit'} {commit.message || ''}
                          </span>
                        )}
                      </li>
                    ))}
                    {commitRows.length === 0 && (
                      <li className="muted-text">
                        {latestActivity?.data?.commits_status === 'unavailable'
                          ? 'Commit table unavailable in connected Coral GitHub source.'
                          : 'No commit updates available.'}
                      </li>
                    )}
                  </ul>
                </section>
              </div>
            </>
          )}
        </article>
      </section>
    )
  }

  const renderProjects = () => (
    <section className="page-section">
      <article className="card page-card">
        <p className="muted-text page-intro">Project-level delivery risk with linked issue and PR evidence.</p>

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

        {filteredProjects.length === 0 && <EmptyState text="No projects found. Refresh planning data or check source status." />}

        {filteredProjects.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Project Leads</th>
                  <th>Contributor</th>
                  <th>Status</th>
                  <th>Risk</th>
                  <th>Issues</th>
                  <th>PRs</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredProjects.map((project) => (
                  <tr key={project.project_id} className="clickable-row" onClick={() => setSelectedProject(project)}>
                    <td>{project.project_name}</td>
                    <td>{project.project_owner_lead || '-'}</td>
                    <td>{project.project_owner_contributor || '-'}</td>
                    <td>{project.project_status || '-'}</td>
                    <td>
                      <div className="risk-stack">
                        <RiskBadge level={project.risk_level} />
                        <span className="risk-score">{project.risk_score}</span>
                      </div>
                    </td>
                    <td>{renderIssueChips(project)}</td>
                    <td>{renderPrChips(project)}</td>
                    <td>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={(e) => {
                          e.stopPropagation()
                          setSelectedProject(project)
                        }}
                      >
                        View details
                      </Button>
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
      {ownersLoading && !ownersLoaded && <LoadingState label="Loading owner risk data..." />}

      {ownersError && (
        <article className="card error-card">
          <h3>Could not load owner data</h3>
          <p>{ownersError}</p>
          <Button variant="danger" onClick={() => void loadOwners(true)} loading={ownersLoading}>
            Retry
          </Button>
        </article>
      )}

      {!ownersLoading && !ownersError && (
        <>
          <OwnerRiskChart items={ownerRiskChartItems} />

          <article className="card page-card">
            <p className="muted-text page-intro">Which project leads have the highest delivery risk concentration.</p>

            {owners.leads.length === 0 && <EmptyState text="No owner risk data available yet." />}

            {owners.leads.length > 0 && (
              <div className="table-wrap">
                <table className="data-table compact">
                  <thead>
                    <tr>
                      <th>Project Leads</th>
                      <th>Total projects</th>
                      <th>High-risk projects</th>
                      <th>Contributors needing follow-up</th>
                      <th>Highest-risk project</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {owners.leads.map((lead) => (
                      <tr key={lead.owner_lead}>
                        <td>{lead.owner_lead}</td>
                        <td>{lead.total_projects_owned}</td>
                        <td>{lead.high_risk_projects}</td>
                        <td>{(lead.contributors_needing_follow_up || []).join(', ') || '-'}</td>
                        <td>{lead.highest_risk_project || '-'}</td>
                        <td>
                          <Button size="sm" variant="secondary" onClick={() => setSelectedLead(lead)}>
                            View owner details
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </article>
        </>
      )}
    </section>
  )

  const renderActions = () => (
    <section className="page-section">
      <article className="card page-card">
          <div className="card-head">
            <p className="muted-text page-intro">Targeted follow-ups for HIGH/CRITICAL projects, refreshed from current data.</p>
            <div className="action-row">
              <Button onClick={() => void refreshActionQueue()} loading={remindersLoading}>
                Refresh reminders
              </Button>
            </div>
          </div>

        <div className="kpi-grid reminders-kpis">
          <StatCard label="Reminders ready" value={reminders.length} />
          <StatCard
            label="Contributors affected"
            value={new Set(reminders.map((r) => r.project_owner_contributor || '(unassigned contributor)')).size}
          />
          <StatCard label="High-risk projects covered" value={new Set(reminders.map((r) => r.project_id)).size} />
        </div>
      </article>

      {remindersLoading && !remindersLoaded && <LoadingState label="Loading reminders..." />}

      {remindersError && (
        <article className="card error-card">
          <h3>Could not load reminders</h3>
          <p>{remindersError}</p>
          <Button variant="danger" onClick={() => void loadReminders(true)} loading={remindersLoading}>
            Retry
          </Button>
        </article>
      )}

      {!remindersLoading && !remindersError && reminders.length === 0 && (
        <EmptyState text="No high-risk reminders needed right now." />
      )}

      {!remindersLoading && !remindersError && remindersByLead.map(([lead, items]) => (
        <article key={lead} className="card page-card">
          <h4 className="section-title">{lead}</h4>
          <div className="reminder-grid">
            {items.map((reminder) => {
              const key = `${reminder.project_id}-${reminder.project_owner_contributor || 'na'}`
              const isCopied = copiedReminderIds.has(key)
              const emailMeta = resolveReminderEmail(reminder)
              const isFocusedReminder = actionsFocusProjectId === reminder.project_id

              return (
                <div
                  key={key}
                  id={reminderCardId(reminder.project_id)}
                  className={`reminder-card${isFocusedReminder ? ' reminder-card-focus' : ''}`}
                >
                  <div className="reminder-head">
                    <h5>{reminder.project_name}</h5>
                    <RiskBadge level={reminder.risk_level} />
                  </div>

                  <p>
                    <strong>Contributor:</strong> {reminder.project_owner_contributor || '-'}
                  </p>
                  <p className="muted-text small-text">Suggested follow-up message:</p>

                  <pre>{reminder.google_chat_text}</pre>

                  <div className="action-row">
                    <Button
                      variant={isCopied ? 'success' : 'secondary'}
                      size="sm"
                      onClick={() => void copyText(key, reminder.google_chat_text)}
                    >
                      {isCopied ? 'Copied' : 'Copy'}
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

                  {!emailMeta.canEmail && <p className="muted-text small-text">Contributor email unavailable</p>}
                </div>
              )
            })}
          </div>
        </article>
      ))}
    </section>
  )

  const renderAssistant = () => (
    <section className="page-section assistant-page">
      <article className="assistant-chat-shell">
        <div className="assistant-chat-head">
          <div className="assistant-head-badges">
            {chatMessages.length > 0 && <span className="muted-text small-text">{chatMessages.length} messages</span>}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="assistant-new-chat-btn"
              onClick={() => setChatMessages([])}
              disabled={chatLoading || chatMessages.length === 0}
            >
              New chat
            </Button>
          </div>
        </div>

        <div ref={assistantChatLogRef} className="assistant-chat-log">
          {chatMessages.length === 0 && !chatLoading && (
            <div className="assistant-empty-state">
              <h4>What are you working on?</h4>
              <p className="muted-text">Ask Omni about risk, blockers, owners, and follow-up actions.</p>
            </div>
          )}

          {chatMessages.map((message, index) => {
            const response = message.response
            const reminder = normalizeAgentReminder(response?.reminder)
            const mailtoUrl = String(response?.email_draft?.mailto_url || reminder?.mailto_url || '')
            const roleLabel = message.role === 'assistant' ? 'Omni' : 'You'

            return (
              <div key={`${message.role}-${index}`} className={`assistant-turn ${message.role}`}>
                <div className={`assistant-avatar ${message.role}`}>
                  {message.role === 'assistant' ? (
                    <img src="/brand/omnisprint_mark_dotgrid.svg" alt="omnisprint" className="assistant-avatar-logo" />
                  ) : (
                    'y'
                  )}
                </div>
                <div className="assistant-turn-content">
                  <div className="assistant-turn-meta">
                    <span>{roleLabel}</span>
                  </div>

                  <div className={`assistant-turn-bubble ${message.role}`}>
                    <div className="chat-message-text">{renderMarkdownText(response?.answer || message.text)}</div>

                    {message.role === 'assistant' && response && (
                      <>
                        {(response.recommended_actions || []).length > 0 && (
                          <div className="assistant-inline-card">
                            <h5>Recommended actions</h5>
                            <ul className="evidence-list">
                              {(response.recommended_actions || []).slice(0, 5).map((action, idx) => (
                                <li key={`assistant-action-${idx}`}>{action}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {reminder && (
                          <div className="assistant-inline-card reminder-inline-card">
                            <h5>Follow-up draft</h5>
                            <pre>{reminder.google_chat_text}</pre>
                            <div className="action-row">
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => void copyPlainText(reminder.google_chat_text || '', 'Message copied')}
                              >
                                Copy
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => openMailtoUrl(mailtoUrl || null)}
                                disabled={!mailtoUrl}
                              >
                                Open email draft
                              </Button>
                            </div>
                          </div>
                        )}

                        <div className="action-row assistant-inline-actions">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => void copyPlainText(response.answer || message.text, 'Omni answer copied')}
                          >
                            Copy answer
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )
          })}

          {chatLoading && (
            <div className="assistant-turn assistant">
              <div className="assistant-avatar assistant">
                <img src="/brand/omnisprint_mark_dotgrid.svg" alt="omnisprint" className="assistant-avatar-logo" />
              </div>
              <div className="assistant-turn-content">
                <div className="assistant-turn-meta">
                  <span>Omni</span>
                </div>
                <div className="assistant-turn-bubble assistant typing">
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                </div>
              </div>
            </div>
          )}
        </div>

        {chatMessages.length === 0 && !chatLoading && (
          <div className="assistant-chat-prompts">
            {SUGGESTED_PROMPTS.slice(0, 4).map((prompt) => (
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
        )}

        <form className="assistant-chat-form" onSubmit={onChatSubmit}>
          <textarea
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={onChatInputKeyDown}
            placeholder="Ask anything"
            rows={1}
          />
          <div className="assistant-chat-form-actions">
            <Button
              type="submit"
              loading={chatLoading}
              className="assistant-send-btn"
              title="Send"
              aria-label="Send message"
              disabled={!chatLoading && !chatInput.trim()}
            >
              <svg viewBox="0 0 24 24" role="img" focusable="false" aria-hidden="true">
                <path fill="currentColor" d="M6.75 17.25a.75.75 0 0 1 0-1.5h8.69L5.47 5.78a.75.75 0 1 1 1.06-1.06l9.97 9.97V6a.75.75 0 0 1 1.5 0v10.5a.75.75 0 0 1-.75.75H6.75Z" />
              </svg>
            </Button>
          </div>
        </form>
      </article>
    </section>
  )

  const renderPage = () => {
    if (activeNav === 'overview') return renderOverview()
    if (activeNav === 'latest_activity') return renderLatestActivity()
    if (activeNav === 'projects') return renderProjects()
    if (activeNav === 'owners') return renderOwners()
    if (activeNav === 'actions') return renderActions()
    if (activeNav === 'assistant') return renderAssistant()
    return renderOverview()
  }

  const projectReminder = selectedProject ? remindersByProjectId.get(selectedProject.project_id) : undefined
  const showReminderCTA = selectedProject && isHighRisk(selectedProject.risk_level)
  const activeViewLabel = NAV_ITEMS.find((item) => item.key === activeNav)?.label || 'Overview'

  return (
    <>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <img src="/brand/omnisprint_logo_dotgrid_horizontal.svg" alt="omnisprint logo" className="brand-logo" />
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
            <div className="sidebar-contact">
              <p className="sidebar-contact-title">Contact us</p>
              <div className="sidebar-contact-links">
                <a href="https://github.com/rohanmalhotracodes/OmniSprint" target="_blank" rel="noreferrer">
                  Github
                </a>
                <span className="sidebar-contact-sep">·</span>
                <a href="https://www.linkedin.com/in/rohanmalhotracodes/" target="_blank" rel="noreferrer">
                  Linkedin
                </a>
              </div>
            </div>
          </div>
        </aside>

        <main className="main-content">
          <header className="topbar">
            <div className="topbar-title">
              <div>
                <h2>{activeViewLabel}</h2>
              </div>
            </div>
            <div className="topbar-meta">
              <span className="meta-pill">Workspace: Oppia</span>
              <span className="meta-pill">Last synced: {lastSynced}</span>
              <Button variant="secondary" onClick={() => void handleRefresh()} loading={coreRefreshing}>
                Refresh
              </Button>
            </div>
          </header>

          {coreLoading && <LoadingState label="Loading omnisprint dashboard..." />}

          {!coreLoading && coreError && (
            <section className="page-section">
              <article className="card error-card">
                <h3>Some data could not be loaded</h3>
                <p>{coreError}</p>
                <Button variant="danger" onClick={() => void loadCoreData(false)}>
                  Retry
                </Button>
              </article>
            </section>
          )}

          {!coreLoading && !coreError && renderPage()}
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
                {selectedLeadProjects.length === 0 && <EmptyState text="No projects available." />}
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
                              <div className="risk-stack">
                                <RiskBadge level={project.risk_level} />
                                <span className="risk-score">{project.risk_score}</span>
                              </div>
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
                <div>
                  <h3>{selectedProject.project_name}</h3>
                  <p className="muted-text">
                    Project Leads: {selectedProject.project_owner_lead || '-'} · Contributor:{' '}
                    {selectedProject.project_owner_contributor || '-'}
                  </p>
                </div>
                <div className="action-row">
                  <RiskBadge level={selectedProject.risk_level} />
                  <span className="score-display-inline">{selectedProject.risk_score}</span>
                  <Button variant="secondary" size="sm" onClick={() => setSelectedProject(null)}>
                    Close
                  </Button>
                </div>
              </div>

              <section className="drawer-section">
                <h4>Suggested actions</h4>
                {(selectedProject.recommendations || []).length === 0 && (
                  <p>No suggested actions available for this project yet.</p>
                )}
                {(selectedProject.recommendations || []).length > 0 && (
                  <ul className="evidence-list">
                    {(selectedProject.recommendations || []).map((rec, idx) => (
                      <li key={`rec-${idx}`}>{rec}</li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="drawer-section">
                <h4>Subtasks</h4>
                {selectedProject.subtasks.length === 0 && <EmptyState text="No subtasks available." />}
                {selectedProject.subtasks.length > 0 && (
                  <div className="table-wrap">
                    <table className="data-table compact">
                      <thead>
                        <tr>
                          <th>Subtask</th>
                          <th>Issue</th>
                          <th>PR</th>
                          <th>Status</th>
                          <th>Assignee</th>
                          <th>Estimated date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedProject.subtasks.map((subtask, idx) => {
                          const issueNums = subtask.github_issue_numbers || []
                          const prNums = Array.from(
                            new Set([
                              ...(subtask.github_pr_numbers || []),
                              ...(subtask.derived_related_pr_numbers || []),
                            ]),
                          )
                          return (
                            <tr key={`${selectedProject.project_id}-subtask-${idx}`}>
                            <td>
                              {subtask.subtask && /^https?:\/\//i.test(subtask.subtask) ? (
                                <a href={subtask.subtask} target="_blank" rel="noreferrer">
                                  {subtask.subtask}
                                </a>
                              ) : (
                                subtask.subtask || '-'
                              )}
                            </td>
                            <td>{renderSubtaskRefChips('issue', issueNums)}</td>
                            <td>{renderSubtaskRefChips('pr', prNums)}</td>
                            <td>{subtask.status || '-'}</td>
                            <td>{subtask.assignee || '-'}</td>
                            <td>{prettyDate(subtask.estimated_completion_date)}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="drawer-section">
                <h4>CI/test evidence</h4>
                {(selectedProject.ci_evidence || []).length === 0 && (
                  <p>No CI/test failure evidence found for this project.</p>
                )}
                {(selectedProject.ci_evidence || []).length > 0 && (
                  <div className="table-wrap">
                    <table className="data-table compact">
                      <thead>
                        <tr>
                          <th>Status</th>
                          <th>Name</th>
                          <th>Summary</th>
                          <th>Updated</th>
                          <th>Link</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(selectedProject.ci_evidence || []).map((item, idx) => (
                          <tr key={`ci-${idx}`}>
                            <td>{item.status || '-'}</td>
                            <td>{item.name || '-'}</td>
                            <td>{item.summary || '-'}</td>
                            <td>{prettyDateTime(item.updated_at)}</td>
                            <td>
                              {item.html_url ? (
                                <a href={item.html_url} target="_blank" rel="noreferrer">
                                  Open log
                                </a>
                              ) : (
                                '-'
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="drawer-section">
                <h4>Reminder</h4>
                {showReminderCTA && (
                  <>
                    {!projectReminder && (
                      <>
                        <p className="muted-text">Follow-up recommended.</p>
                        <div className="action-row">
                          <Button
                            loading={generatingProjectId === selectedProject.project_id}
                            onClick={() => void generateReminderForProject(selectedProject)}
                          >
                            Generate this project reminder
                          </Button>
                        </div>
                      </>
                    )}

                    {projectReminder && (
                      <div className="action-row">
                        <span className="muted-text small-text">Reminder ready in Actions.</span>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => openActionsQueueForProject(projectReminder.project_id)}
                        >
                          Open actions queue
                        </Button>
                      </div>
                    )}

                    {projectReminder && (
                      <div className="reminder-preview">
                        <pre>{projectReminder.google_chat_text}</pre>
                        <div className="action-row">
                          <Button
                            variant={copiedReminderIds.has(`drawer-${projectReminder.project_id}`) ? 'success' : 'secondary'}
                            size="sm"
                            onClick={() =>
                              void copyText(`drawer-${projectReminder.project_id}`, projectReminder.google_chat_text)
                            }
                          >
                            {copiedReminderIds.has(`drawer-${projectReminder.project_id}`)
                              ? 'Copied'
                              : 'Copy'}
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

                {!showReminderCTA && <p>No reminder needed — project appears on track.</p>}
              </section>
            </aside>
          </div>
        )}
      </div>

      <Toast toasts={toasts} onDismiss={dismissToast} />
    </>
  )
}
