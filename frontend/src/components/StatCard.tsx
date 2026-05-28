import React from 'react'

type StatCardProps = {
  label: string
  value: number | string
  hint?: string
}

export default function StatCard({ label, value, hint }: StatCardProps) {
  return (
    <article className="card stat-card">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {hint ? <p className="stat-hint">{hint}</p> : null}
    </article>
  )
}

