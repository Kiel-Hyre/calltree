<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import StatTile from '@/components/StatTile.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const data = ref(null)
const error = ref('')
let timer = null

async function load() {
  try {
    data.value = await api.get('/api/overview/')
    error.value = ''
  } catch (err) {
    error.value = err.message
  }
}

onMounted(() => {
  load()
  // An active drill changes second by second; 10s keeps the landing page
  // current without the cost of the full monitor poll.
  timer = setInterval(load, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <PageHeader
      title="Operations overview"
      subtitle="Digital Service Operations — earthquake readiness"
    >
      <template #actions>
        <RouterLink to="/drills" class="btn-primary">Drills</RouterLink>
      </template>
    </PageHeader>

    <AlertBanner :message="error" @dismiss="error = ''" />

    <div v-if="data">
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Active personnel" :value="data.counts.employees" />
        <StatTile label="Geofenced sites" :value="data.counts.locations" />
        <StatTile label="Total drills" :value="data.counts.drills" />
        <StatTile
          label="Active now"
          :value="data.counts.active_drills"
          :tone="data.counts.active_drills ? 'alert' : 'neutral'"
        />
      </div>

      <section v-if="data.active_drills.length" class="mt-8">
        <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-500">
          Live drills
        </h2>
        <div class="grid gap-4 lg:grid-cols-2">
          <RouterLink
            v-for="item in data.active_drills"
            :key="item.drill.id"
            :to="`/drills/${item.drill.id}`"
            class="card card-pad transition hover:border-ink-400"
          >
            <div class="flex items-start justify-between gap-3">
              <div>
                <p class="font-semibold">{{ item.drill.name }}</p>
                <p class="mt-0.5 text-xs text-ink-500 dark:text-ink-400">
                  Started {{ formatDateTime(item.drill.started_at) }}
                </p>
              </div>
              <span class="badge bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100">
                <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                Live
              </span>
            </div>

            <div class="mt-4 grid grid-cols-4 gap-3 text-center">
              <div>
                <p class="text-xl font-bold tabular-nums text-ok-600">{{ item.statistics.safe }}</p>
                <p class="text-xs text-ink-500">Safe</p>
              </div>
              <div>
                <p class="text-xl font-bold tabular-nums text-alert-600">{{ item.statistics.help }}</p>
                <p class="text-xs text-ink-500">Help</p>
              </div>
              <div>
                <p class="text-xl font-bold tabular-nums text-warn-600">{{ item.statistics.pending }}</p>
                <p class="text-xs text-ink-500">Pending</p>
              </div>
              <div>
                <p class="text-xl font-bold tabular-nums">{{ item.statistics.response_rate }}%</p>
                <p class="text-xs text-ink-500">Response</p>
              </div>
            </div>
          </RouterLink>
        </div>
      </section>

      <div class="mt-8 grid gap-6 lg:grid-cols-2">
        <section class="card">
          <h2 class="border-b border-ink-200 px-5 py-3 text-sm font-semibold dark:border-ink-800">
            Recent seismic events
          </h2>
          <ul class="divide-y divide-ink-200 dark:divide-ink-800">
            <li
              v-for="event in data.recent_events"
              :key="event.id"
              class="flex items-center justify-between gap-3 px-5 py-3"
            >
              <div class="min-w-0">
                <p class="truncate text-sm font-medium">
                  M{{ event.magnitude }} · {{ event.place || 'Unnamed region' }}
                </p>
                <p class="text-xs text-ink-500">{{ formatDateTime(event.occurred_at) }}</p>
              </div>
              <span
                class="badge shrink-0"
                :class="event.triggered
                  ? 'bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100'
                  : 'bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300'"
              >{{ event.triggered ? 'Triggered' : 'Below filter' }}</span>
            </li>
            <li v-if="!data.recent_events.length" class="px-5 py-6 text-center text-sm text-ink-500">
              No events ingested yet.
            </li>
          </ul>
        </section>

        <section class="card">
          <h2 class="border-b border-ink-200 px-5 py-3 text-sm font-semibold dark:border-ink-800">
            Activity log
          </h2>
          <ul class="divide-y divide-ink-200 dark:divide-ink-800">
            <li v-for="entry in data.recent_activity" :key="entry.id" class="px-5 py-3">
              <p class="text-sm">
                <span class="font-mono text-xs text-ink-500">{{ entry.action }}</span>
                <span class="ml-2">{{ entry.target }}</span>
              </p>
              <p class="text-xs text-ink-500">
                {{ entry.actor_name }} · {{ formatDateTime(entry.created_at) }}
              </p>
            </li>
            <li v-if="!data.recent_activity.length" class="px-5 py-6 text-center text-sm text-ink-500">
              Nothing logged yet.
            </li>
          </ul>
        </section>
      </div>
    </div>

    <p v-else class="text-sm text-ink-500">Loading…</p>
  </div>
</template>
