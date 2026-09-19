import { onUnmounted, ref } from 'vue'

/**
 * Poll an async task on an interval, without the failure modes of a bare
 * setInterval:
 *
 *   - **No overlap.** The next tick is scheduled only once the previous one
 *     settles. A bare interval fires regardless, so a slow server piles up
 *     requests until they exhaust the browser's ~6-connections-per-host
 *     budget — at which point clicks stop producing anything and the page
 *     looks frozen until you reload.
 *   - **Aborts in flight.** Leaving the page cancels the request instead of
 *     letting it hold a connection.
 *   - **Pauses in a hidden tab** and refreshes on return, so a backgrounded
 *     dashboard is not still polling every five seconds.
 *   - **Backs off after failures** instead of hammering a server that is
 *     already struggling.
 *
 * The task receives an AbortSignal to pass to the API client.
 */
export function usePolling(task, { interval = 5000, maxBackoff = 6, immediate = true } = {}) {
  const pending = ref(false)
  const failures = ref(0)

  let timer = null
  let controller = null
  let stopped = false

  const isAbort = (err) => err?.aborted === true || err?.name === 'AbortError'

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function schedule() {
    clearTimer()
    if (stopped) return
    // Slow down while failing: 1x, 2x, 3x … capped at maxBackoff.
    const delay = interval * Math.min(failures.value + 1, maxBackoff)
    timer = setTimeout(run, delay)
  }

  async function run() {
    if (stopped) return
    clearTimer()

    // A hidden tab does not need fresh data; check back at the base interval.
    if (typeof document !== 'undefined' && document.hidden) {
      timer = setTimeout(run, interval)
      return
    }

    // Supersede anything still in flight, so only one request is ever open.
    controller?.abort()
    const ticket = new AbortController()
    controller = ticket
    pending.value = true

    try {
      await task(ticket.signal)
      if (controller === ticket) failures.value = 0
    } catch (err) {
      if (controller === ticket && !isAbort(err)) failures.value += 1
    } finally {
      // Only the newest request owns the schedule; a superseded one exits.
      if (controller === ticket) {
        pending.value = false
        controller = null
        schedule()
      }
    }
  }

  function stop() {
    stopped = true
    clearTimer()
    controller?.abort()
    controller = null
    pending.value = false
  }

  function start() {
    stopped = false
    return run()
  }

  function onVisibilityChange() {
    if (!document.hidden && !stopped) run()
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  onUnmounted(() => {
    stop()
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  })

  if (immediate) run()

  return { pending, failures, refresh: run, stop, start }
}

export default usePolling
