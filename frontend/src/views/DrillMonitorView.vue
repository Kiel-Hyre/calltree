<script setup>
/**
 * Interactive Dashboard Monitoring: the live status table the Safety Officer
 * watches while responses stream in.
 */
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, isAborted } from '@/api/client'
import { usePolling } from '@/composables/usePolling'
import { formatDateTime, formatSeconds, formatTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import StatTile from '@/components/StatTile.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const props = defineProps({ id: { type: [String, Number], required: true } })

const monitor = ref(null)
const error = ref('')
const notice = ref('')
const filter = ref('all')
const busy = ref(false)

const drill = computed(() => monitor.value?.drill)
const stats = computed(() => monitor.value?.statistics)

const participants = computed(() => {
  const rows = monitor.value?.participants ?? []
  if (filter.value === 'all') return rows
  if (filter.value === 'outstanding') {
    return rows.filter((p) => !['safe', 'help'].includes(p.status))
  }
  return rows.filter((p) => p.status === filter.value)
})

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'outstanding', label: 'Outstanding' },
  { key: 'safe', label: 'Safe' },
  { key: 'help', label: 'Needs help' },
  { key: 'non_compliant', label: 'Non-compliant' },
]

async function load(signal) {
  try {
    monitor.value = await api.get(`/api/drills/${props.id}/monitor/`, null, signal)
    error.value = ''
    // A finished drill cannot change again; stop polling rather than
    // refreshing a static page every five seconds forever.
    if (monitor.value.drill.status !== 'active') stop()
  } catch (err) {
    if (isAborted(err)) return
    error.value = err.message
    throw err
  }
}

const { pending, refresh, start, stop } = usePolling(load, { interval: 5000 })

async function act(path, confirmText) {
  if (confirmText && !window.confirm(confirmText)) return
  busy.value = true
  error.value = ''
  try {
    const result = await api.post(`/api/drills/${props.id}/${path}/`)
    if (path === 'activate') {
      notice.value = `Alert initiated: ${result.notifications_queued} message(s) queued.`
    } else if (path === 'sweep') {
      notice.value = `${result.reminders} reminder(s) sent, ${result.flagged} flagged, ${result.escalated} escalation message(s).`
    } else {
      notice.value = 'Drill updated.'
    }
    // Activating restarts a poll that stop() may have ended.
    start()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function override(participant, status) {
  try {
    await api.post(`/api/participants/${participant.id}/override/`, {
      status,
      note: 'Set manually by the Safety Officer',
    })
    await refresh()
  } catch (err) {
    if (!isAborted(err)) error.value = err.message
  }
}

function exportCsv() {
  window.location.href = `/api/drills/${props.id}/report.csv/`
}
</script>

<template>
  <div>
    <RouterLink
      to="/drills"
      class="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-ink-500
             transition hover:text-ink-900 dark:text-ink-400 dark:hover:text-ink-100"
    >
      <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <path d="M15 18l-6-6 6-6" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      All drills
    </RouterLink>

    <template v-if="drill">
      <PageHeader :title="drill.name" :subtitle="drill.location_names.join(', ')">
        <template #actions>
          <button v-if="drill.status === 'draft'" class="btn-danger" :disabled="busy"
            @click="act('activate', `Initiate the call tree for “${drill.name}”? Alerts go out immediately.`)">
            Initiate call tree alert
          </button>
          <template v-if="drill.status === 'active'">
            <button class="btn-ghost" :disabled="busy" @click="act('sweep')">
              Re-broadcast &amp; escalate
            </button>
            <button class="btn-primary" :disabled="busy"
              @click="act('complete', 'Close this drill? Everyone still pending is marked non-compliant.')">
              Complete drill
            </button>
          </template>
          <button class="btn-ghost" @click="exportCsv">Export CSV</button>
        </template>
      </PageHeader>

      <AlertBanner :message="error" @dismiss="error = ''" />
      <AlertBanner :message="notice" tone="ok" @dismiss="notice = ''" />

      <div
        v-if="drill.status === 'active'"
        class="mb-5 flex flex-wrap items-center gap-3 rounded-lg border border-alert-300 bg-alert-50
               px-4 py-3 text-sm dark:border-alert-700 dark:bg-alert-700/15"
      >
        <span class="badge bg-alert-600 text-white">
          <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />Live
        </span>
        <span>Started {{ formatTime(drill.started_at) }}</span>
        <span>·</span>
        <span>
          Compliance deadline {{ formatTime(stats.deadline) }}
          <strong v-if="!stats.window_open" class="text-alert-700 dark:text-alert-100">(elapsed)</strong>
        </span>
      </div>

      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile label="Response rate" :value="`${stats.response_rate}%`"
          :hint="`${stats.responded} of ${stats.total} accounted for`"
          :tone="stats.response_rate === 100 ? 'ok' : 'warn'" />
        <StatTile label="Safe" :value="stats.safe" tone="ok" />
        <StatTile label="Needs help" :value="stats.help" :tone="stats.help ? 'alert' : 'neutral'" />
        <StatTile label="Outstanding" :value="stats.pending + stats.non_compliant"
          :tone="stats.non_compliant ? 'alert' : 'warn'" :hint="`${stats.non_compliant} past deadline`" />
        <StatTile label="Avg. response" :value="formatSeconds(stats.avg_response_seconds)"
          :hint="stats.avg_dispatch_latency_seconds !== null
            ? `Dispatch ${formatSeconds(stats.avg_dispatch_latency_seconds)}`
            : 'From alert delivery to reply'" />
      </div>

      <div class="mt-6 flex flex-wrap gap-2">
        <button v-for="option in FILTERS" :key="option.key" class="btn-ghost px-3 py-1.5 text-xs"
          :class="filter === option.key ? 'border-ink-900 bg-ink-900 text-white dark:border-ink-100 dark:bg-ink-100 dark:text-ink-900' : ''"
          @click="filter = option.key">
          {{ option.label }}
        </button>
      </div>

      <div class="card mt-3 overflow-x-auto">
        <table class="w-full min-w-[54rem]">
          <thead class="border-b border-ink-200 dark:border-ink-800">
            <tr>
              <th class="table-head">Personnel</th>
              <th class="table-head">Location</th>
              <th class="table-head">Status</th>
              <th class="table-head">Notified</th>
              <th class="table-head">Responded</th>
              <th class="table-head">Latency</th>
              <th class="table-head">Reminders</th>
              <th class="table-head text-right">Reconcile</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-ink-200 dark:divide-ink-800">
            <tr v-for="row in participants" :key="row.id">
              <td class="table-cell">
                <p class="font-medium">{{ row.employee_name }}</p>
                <p class="text-xs text-ink-500">
                  {{ row.employee_code }} · tier {{ row.escalation_tier }}
                </p>
              </td>
              <td class="table-cell text-xs">{{ row.location_name }}</td>
              <td class="table-cell">
                <StatusBadge :status="row.status" :label="row.status_display" />
              </td>
              <td class="table-cell text-xs tabular-nums">{{ formatTime(row.notified_at) }}</td>
              <td class="table-cell text-xs tabular-nums">{{ formatTime(row.responded_at) }}</td>
              <td class="table-cell text-xs tabular-nums">
                {{ formatSeconds(row.response_latency_seconds) }}
              </td>
              <td class="table-cell text-xs tabular-nums">{{ row.reminders_sent }}</td>
              <td class="table-cell text-right">
                <div class="inline-flex gap-1">
                  <button class="btn-ghost px-2 py-1 text-xs" title="Mark safe"
                    @click="override(row, 'safe')">Safe</button>
                  <button class="btn-ghost px-2 py-1 text-xs" title="Mark as needing help"
                    @click="override(row, 'help')">Help</button>
                </div>
              </td>
            </tr>
            <tr v-if="!participants.length">
              <td colspan="8" class="px-4 py-10 text-center text-sm text-ink-500">
                No personnel match this filter.
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-500">
        <span v-if="drill.status === 'active'">
          Auto-refreshing every 5 seconds{{ pending ? ' · updating…' : '' }}
        </span>
        <span v-else>
          Drill closed — live updates stopped.
          <button class="underline hover:text-ink-900 dark:hover:text-ink-100" @click="refresh">
            Refresh now
          </button>
        </span>
        <span>· server time {{ formatDateTime(monitor.server_time) }}</span>
      </p>
    </template>

    <template v-else>
      <AlertBanner :message="error" @dismiss="error = ''" />
      <p v-if="!error" class="text-sm text-ink-500">Loading…</p>
    </template>
  </div>
</template>
