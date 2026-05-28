import React from 'react'
import DashboardBarChart from './DashboardBarChart'

type RiskDistributionProps = {
  counts: Record<'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW', number>
}

export default function RiskDistribution({ counts }: RiskDistributionProps) {
  return (
    <DashboardBarChart
      title="Risk distribution"
      subtitle="Projects grouped by delivery-risk level."
      scale="total"
      items={[
        { label: 'Critical', value: counts.CRITICAL || 0, colorClass: 'critical' },
        { label: 'High', value: counts.HIGH || 0, colorClass: 'high' },
        { label: 'Medium', value: counts.MEDIUM || 0, colorClass: 'medium' },
        { label: 'Low', value: counts.LOW || 0, colorClass: 'low' },
      ]}
    />
  )
}
