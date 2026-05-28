import React from 'react'

type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | string

export function riskBadgeClass(level?: RiskLevel) {
  const normalized = String(level || 'LOW').toUpperCase()
  if (normalized === 'CRITICAL') return 'risk-badge critical'
  if (normalized === 'HIGH') return 'risk-badge high'
  if (normalized === 'MEDIUM') return 'risk-badge medium'
  return 'risk-badge low'
}

type RiskBadgeProps = {
  level?: RiskLevel
}

export default function RiskBadge({ level }: RiskBadgeProps) {
  const normalized = String(level || 'LOW').toUpperCase()
  return <span className={riskBadgeClass(normalized)}>{normalized}</span>
}

