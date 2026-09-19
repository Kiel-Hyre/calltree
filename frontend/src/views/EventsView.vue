<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, listOf } from '@/api/client'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const router = useRouter()
const events = ref([])
const error = ref('')
const notice = ref('')
const busy = ref(false)
const expanded = ref(null)
const proximity = ref([])

const sim = reactive({
  magnitude: 6.2,
  latitude: 14.6,
  longitude: 121.05,
  place: 'Simulated epicentre, Metro Manila',
  auto_trigger: false,
})

async function load() {
  try {
    events.value = listOf(await api.get('/api/events/'))
  } catch (err) {
    error.value = err.message
  }
}

async function simulate() {
  busy.value = true
  error.value = ''
  try {
    const result = await api.post('/api/events/simulate/', { ...sim })
    const hits = result.matches.filter((m) => m.triggered).length
    notice.value = result.drill
      ? `Trigger Filter matched ${hits} location(s); drill #${result.drill.id} activated.`
      : `Event recorded. Trigger Filter matched ${hits} location(s).`
    if (result.drill) router.push(`/drills/${result.drill.id}`)
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function inspect(event) {
  if (expanded.value === event.id) {
    expanded.value = null
    return
  }
  try {
    proximity.value = await api.get(`/api/events/${event.id}/proximity/`)
    expanded.value = event.id
  } catch (err) {
    error.value = err.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="Seismic feed"
      subtitle="Events ingested from the USGS Earthquake Notification Service"
    />

    <AlertBanner :message="error" @dismiss="error = ''" />
    <AlertBanner :message="notice" tone="ok" @dismiss="notice = ''" />

    <form class="card card-pad mb-6" @submit.prevent="simulate">
      <h2 class="mb-1 text-sm font-semibold uppercase tracking-wide text-ink-500">
        Simulate a USGS alert
      </h2>
      <p class="mb-4 text-xs text-ink-500">
        Injects a synthetic event and runs the Trigger Filter against every active geofence.
      </p>

      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label class="label" for="mag">Magnitude</label>
          <input id="mag" v-model.number="sim.magnitude" type="number" step="0.1" min="0" max="10" class="field" />
        </div>
        <div>
          <label class="label" for="lat">Latitude</label>
          <input id="lat" v-model.number="sim.latitude" type="number" step="any" class="field" />
        </div>
        <div>
          <label class="label" for="lon">Longitude</label>
          <input id="lon" v-model.number="sim.longitude" type="number" step="any" class="field" />
        </div>
        <div>
          <label class="label" for="place">Place</label>
          <input id="place" v-model="sim.place" class="field" />
        </div>
      </div>

      <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
        <label class="flex items-center gap-2 text-sm">
          <input v-model="sim.auto_trigger" type="checkbox" class="accent-ink-900" />
          Activate the call tree automatically if the filter matches
        </label>
        <button class="btn-primary" type="submit" :disabled="busy">
          {{ busy ? 'Evaluating…' : 'Run simulation' }}
        </button>
      </div>
    </form>

    <div class="card overflow-hidden">
      <table class="w-full">
        <thead class="border-b border-ink-200 dark:border-ink-800">
          <tr>
            <th class="table-head">Event</th>
            <th class="table-head">Occurred</th>
            <th class="table-head">Source</th>
            <th class="table-head">Filter</th>
            <th class="table-head text-right">Proximity</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-ink-200 dark:divide-ink-800">
          <template v-for="event in events" :key="event.id">
            <tr>
              <td class="table-cell">
                <p class="font-medium">M{{ event.magnitude }} · {{ event.place || 'Unnamed region' }}</p>
                <p class="font-mono text-xs text-ink-500">{{ event.usgs_id }}</p>
              </td>
              <td class="table-cell text-xs">{{ formatDateTime(event.occurred_at) }}</td>
              <td class="table-cell text-xs">{{ event.source }}</td>
              <td class="table-cell">
                <span
                  class="badge"
                  :class="event.triggered
                    ? 'bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100'
                    : 'bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300'"
                >{{ event.triggered ? 'Triggered' : 'No match' }}</span>
                <p class="mt-0.5 text-xs text-ink-500">{{ event.evaluation_note }}</p>
              </td>
              <td class="table-cell text-right">
                <button class="btn-ghost px-2 py-1 text-xs" @click="inspect(event)">
                  {{ expanded === event.id ? 'Hide' : 'Show' }}
                </button>
              </td>
            </tr>
            <tr v-if="expanded === event.id" class="bg-ink-50 dark:bg-ink-950/60">
              <td colspan="5" class="px-4 py-3">
                <ul class="space-y-1.5 text-xs">
                  <li v-for="row in proximity" :key="row.location_id" class="flex flex-wrap gap-2">
                    <span class="font-medium">{{ row.location }}</span>
                    <span class="tabular-nums text-ink-500">{{ row.distance_km }} km</span>
                    <span :class="row.triggered ? 'font-semibold text-alert-600' : 'text-ink-500'">
                      {{ row.triggered ? 'TRIGGERED' : row.reason }}
                    </span>
                  </li>
                </ul>
              </td>
            </tr>
          </template>
          <tr v-if="!events.length">
            <td colspan="5" class="px-4 py-10 text-center text-sm text-ink-500">
              No events ingested. Run a simulation, or start
              <code>manage.py poll_usgs</code>.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
