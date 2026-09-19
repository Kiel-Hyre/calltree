/** Shared display helpers. */

export function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatDateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatSeconds(value) {
  if (value === null || value === undefined) return '—'
  if (value < 60) return `${Number(value).toFixed(1)}s`
  const minutes = Math.floor(value / 60)
  const seconds = Math.round(value % 60)
  return `${minutes}m ${seconds}s`
}

/** Tailwind classes per participant status, used by the badge component. */
export const STATUS_STYLES = {
  safe: 'bg-ok-100 text-ok-700 dark:bg-ok-700/20 dark:text-ok-100',
  help: 'bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100',
  pending: 'bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300',
  notified: 'bg-warn-100 text-warn-700 dark:bg-warn-700/20 dark:text-warn-100',
  non_compliant: 'bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100',
  unreachable: 'bg-ink-200 text-ink-700 dark:bg-ink-800 dark:text-ink-300',
}

export const statusClass = (status) => STATUS_STYLES[status] ?? STATUS_STYLES.pending
