import React from 'react'

type LoadingStateProps = {
  label?: string
}

export default function LoadingState({ label = 'Loading dashboard data...' }: LoadingStateProps) {
  return (
    <section className="page-section">
      <article className="card loading-card">
        <p className="muted-text">{label}</p>
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
  )
}

