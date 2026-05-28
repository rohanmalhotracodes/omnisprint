import React from 'react'

type EmptyStateProps = {
  text: string
}

export default function EmptyState({ text }: EmptyStateProps) {
  return <p className="empty-state">{text}</p>
}

