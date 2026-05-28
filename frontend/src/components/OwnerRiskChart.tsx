import React from 'react'
import DashboardBarChart from './DashboardBarChart'

type OwnerRiskItem = {
  owner: string
  highRiskProjects: number
}

type OwnerRiskChartProps = {
  items: OwnerRiskItem[]
}

export default function OwnerRiskChart({ items }: OwnerRiskChartProps) {
  return (
    <DashboardBarChart
      title="Owner risk concentration"
      subtitle="Top project leads by high-risk project count."
      items={items.map((item) => ({
        label: item.owner || '(unassigned lead)',
        value: item.highRiskProjects,
        colorClass: 'high',
      }))}
    />
  )
}
