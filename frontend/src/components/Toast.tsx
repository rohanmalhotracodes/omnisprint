import React from 'react'

export type ToastTone = 'success' | 'error' | 'info'

export type ToastItem = {
  id: number
  message: string
  tone: ToastTone
}

type ToastProps = {
  toasts: ToastItem[]
  onDismiss: (id: number) => void
}

export default function Toast({ toasts, onDismiss }: ToastProps) {
  if (toasts.length === 0) return null

  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.tone}`}>
          <span>{toast.message}</span>
          <button
            type="button"
            className="toast-close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Dismiss notification"
          >
            x
          </button>
        </div>
      ))}
    </div>
  )
}
