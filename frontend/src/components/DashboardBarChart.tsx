import React from 'react'

type ChartItem = {
  label: string
  value: number
  colorClass?: string
}

type DashboardBarChartProps = {
  title: string
  subtitle?: string
  items: ChartItem[]
  scale?: 'max' | 'total'
}

export default function DashboardBarChart({
  title,
  subtitle,
  items,
  scale = 'max',
}: DashboardBarChartProps) {
  const max = Math.max(1, ...items.map((item) => item.value))
  const total = items.reduce((sum, item) => sum + Math.max(0, item.value || 0), 0)

  return (
    <article className="card page-card chart-card">
      <h3 className="section-title">{title}</h3>
      {subtitle ? <p className="muted-text">{subtitle}</p> : null}

      {items.length === 0 ? (
        <p className="empty-state">No chart data available.</p>
      ) : (
        <div className="dashboard-chart-list">
          {items.map((item) => {
            const value = Math.max(0, item.value || 0)
            let width = 0
            if (scale === 'total') {
              width = total > 0 ? (value / total) * 100 : 0
            } else {
              width = max > 0 ? (value / max) * 100 : 0
            }
            return (
              <div key={item.label} className="dashboard-chart-row">
                <div className="dashboard-chart-labels">
                  <span>{item.label}</span>
                  <strong>{item.value}</strong>
                </div>
                <div className="dashboard-chart-track">
                  <div
                    className={`dashboard-chart-fill${item.colorClass ? ` ${item.colorClass}` : ''}`}
                    style={{ width: `${width}%` }}
                  />
                </div>
              </div>
            )
          })}
        </div>
      )}
    </article>
  )
}
