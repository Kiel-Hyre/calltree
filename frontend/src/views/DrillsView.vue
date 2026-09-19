<script setup>
import { onMounted, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api, listOf } from '@/api/client'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const router = useRouter()
const drills = ref([])
const locations = ref([])
const error = ref('')
const details = ref([])
const notice = ref('')
const showForm = ref(false)
const saving = ref(false)

const form = reactive({
  name: '',
  kind: 'drill',
  locations: [],
  message_template: '',
  response_window_minutes: 5,
  reminder_interval_minutes: 2,
  max_reminders: 2,
  send_sms: true,
  send_email: true,
})

async function load() {
  try {
    const [drillPage, locationPage] = await Promise.all([
      api.get('/api/drills/'),
      api.get('/api/locations/', { active: 'true' }),
    ])
    drills.value = listOf(drillPage)
    locations.value = listOf(locationPage)
  } catch (err) {
    error.value = err.message
  }
}

async function create() {
  saving.value = true
  error.value = ''
  details.value = []
  try {
    const drill = await api.post('/api/drills/', { ...form })
    showForm.value = false
    router.push(`/drills/${drill.id}`)
  } catch (err) {
    error.value = err.message
    details.value = err.fieldErrors ?? []
  } finally {
    saving.value = false
  }
}

async function activate(drill) {
  if (!window.confirm(`Initiate the call tree for "${drill.name}"? Alerts go out immediately.`)) return
  try {
    const result = await api.post(`/api/drills/${drill.id}/activate/`)
    notice.value = `Alert initiated: ${result.notifications_queued} message(s) queued for ${result.participants} personnel.`
    await load()
  } catch (err) {
    error.value = err.message
  }
}

const STATUS_TONE = {
  active: 'bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100',
  completed: 'bg-ok-100 text-ok-700 dark:bg-ok-700/20 dark:text-ok-100',
  draft: 'bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300',
  cancelled: 'bg-ink-200 text-ink-600 dark:bg-ink-800 dark:text-ink-400',
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader title="Drills" subtitle="Configure and initiate call tree activations">
      <template #actions>
        <button class="btn-primary" @click="showForm = !showForm">
          {{ showForm ? 'Cancel' : 'New drill' }}
        </button>
      </template>
    </PageHeader>

    <AlertBanner :message="error" :details="details" @dismiss="error = ''" />
    <AlertBanner :message="notice" tone="ok" @dismiss="notice = ''" />

    <form v-if="showForm" class="card card-pad mb-6" @submit.prevent="create">
      <h2 class="mb-4 text-sm font-semibold uppercase tracking-wide text-ink-500">
        Drill parameters
      </h2>

      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <label class="label" for="name">Drill name</label>
          <input id="name" v-model="form.name" class="field" required placeholder="Q3 NSED Exercise" />
        </div>
        <div>
          <label class="label" for="kind">Type</label>
          <select id="kind" v-model="form.kind" class="field">
            <option value="drill">Scheduled drill</option>
            <option value="incident">Live seismic incident</option>
            <option value="test">System test</option>
          </select>
        </div>
      </div>

      <fieldset class="mt-4">
        <legend class="label">Target geofences</legend>
        <div class="flex flex-wrap gap-2">
          <label
            v-for="location in locations"
            :key="location.id"
            class="flex cursor-pointer items-center gap-2 rounded-lg border border-ink-200 px-3 py-2
                   text-sm dark:border-ink-700"
          >
            <input v-model="form.locations" type="checkbox" :value="location.id" class="accent-ink-900" />
            {{ location.name }}
            <span class="text-xs text-ink-500">({{ location.employee_count }})</span>
          </label>
          <p v-if="!locations.length" class="text-sm text-ink-500">
            Add a geofenced location first.
          </p>
        </div>
      </fieldset>

      <div class="mt-4">
        <label class="label" for="template">Alert message</label>
        <textarea
          id="template"
          v-model="form.message_template"
          class="field font-mono text-xs"
          rows="3"
          placeholder="Leave blank for the standard template."
        />
        <p class="mt-1 text-xs text-ink-500">
          Placeholders: {name} {location} {magnitude} {place} {instruction} {link}
        </p>
      </div>

      <div class="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <label class="label" for="window">Response window (min)</label>
          <input id="window" v-model.number="form.response_window_minutes" type="number" min="1" class="field" />
        </div>
        <div>
          <label class="label" for="interval">Reminder every (min)</label>
          <input id="interval" v-model.number="form.reminder_interval_minutes" type="number" min="1" class="field" />
        </div>
        <div>
          <label class="label" for="max">Max reminders</label>
          <input id="max" v-model.number="form.max_reminders" type="number" min="0" class="field" />
        </div>
      </div>

      <div class="mt-4 flex gap-4 text-sm">
        <label class="flex items-center gap-2">
          <input v-model="form.send_sms" type="checkbox" class="accent-ink-900" /> Send SMS
        </label>
        <label class="flex items-center gap-2">
          <input v-model="form.send_email" type="checkbox" class="accent-ink-900" /> Send email
        </label>
      </div>

      <div class="mt-5 flex justify-end gap-2">
        <button type="button" class="btn-ghost" @click="showForm = false">Cancel</button>
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? 'Saving…' : 'Create drill' }}
        </button>
      </div>
    </form>

    <div class="card overflow-x-auto">
      <table class="w-full min-w-[46rem]">
        <thead class="border-b border-ink-200 dark:border-ink-800">
          <tr>
            <th class="table-head">Drill</th>
            <th class="table-head">Status</th>
            <th class="table-head">Personnel</th>
            <th class="table-head">Started</th>
            <th class="table-head text-right">Action</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-ink-200 dark:divide-ink-800">
          <tr v-for="drill in drills" :key="drill.id">
            <td class="table-cell">
              <RouterLink :to="`/drills/${drill.id}`" class="font-medium hover:underline">
                {{ drill.name }}
              </RouterLink>
              <p class="text-xs text-ink-500">{{ drill.location_names.join(', ') || 'No locations' }}</p>
            </td>
            <td class="table-cell">
              <span class="badge" :class="STATUS_TONE[drill.status]">{{ drill.status }}</span>
            </td>
            <td class="table-cell tabular-nums">{{ drill.participant_count }}</td>
            <td class="table-cell text-xs">{{ formatDateTime(drill.started_at) }}</td>
            <td class="table-cell text-right">
              <button v-if="drill.status === 'draft'" class="btn-danger" @click="activate(drill)">
                Initiate alert
              </button>
              <RouterLink v-else :to="`/drills/${drill.id}`" class="btn-ghost">Monitor</RouterLink>
            </td>
          </tr>
          <tr v-if="!drills.length">
            <td colspan="5" class="px-4 py-10 text-center text-sm text-ink-500">
              No drills yet. Create one to get started.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
