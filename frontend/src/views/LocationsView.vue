<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, listOf } from '@/api/client'
import PageHeader from '@/components/PageHeader.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const locations = ref([])
const error = ref('')
const details = ref([])
const showForm = ref(false)
const editingId = ref(null)
const saving = ref(false)

const blank = () => ({
  name: '',
  code: '',
  address: '',
  latitude: 14.5507,
  longitude: 121.0506,
  radius_km: 250,
  min_magnitude: 5.0,
  evacuation_instruction: '',
  is_active: true,
})
const form = reactive(blank())

async function load() {
  try {
    locations.value = listOf(await api.get('/api/locations/'))
  } catch (err) {
    error.value = err.message
  }
}

function startCreate() {
  Object.assign(form, blank())
  editingId.value = null
  showForm.value = true
}

function startEdit(location) {
  Object.assign(form, location)
  editingId.value = location.id
  showForm.value = true
}

async function save() {
  saving.value = true
  error.value = ''
  details.value = []
  try {
    if (editingId.value) {
      await api.put(`/api/locations/${editingId.value}/`, { ...form })
    } else {
      await api.post('/api/locations/', { ...form })
    }
    showForm.value = false
    await load()
  } catch (err) {
    error.value = err.message
    details.value = err.fieldErrors ?? []
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="Geofenced locations"
      subtitle="Each site carries its own Trigger Filter: magnitude threshold and risk radius"
    >
      <template #actions>
        <button class="btn-primary" @click="startCreate">Add location</button>
      </template>
    </PageHeader>

    <AlertBanner :message="error" :details="details" @dismiss="error = ''" />

    <form v-if="showForm" class="card card-pad mb-6" @submit.prevent="save">
      <h2 class="mb-4 text-sm font-semibold uppercase tracking-wide text-ink-500">
        {{ editingId ? 'Edit location' : 'New location' }}
      </h2>

      <div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <div>
          <label class="label" for="lname">Name</label>
          <input id="lname" v-model="form.name" class="field" required />
        </div>
        <div>
          <label class="label" for="code">Code</label>
          <input id="code" v-model="form.code" class="field" required placeholder="bgc" />
        </div>
        <div>
          <label class="label" for="address">Address</label>
          <input id="address" v-model="form.address" class="field" />
        </div>
        <div>
          <label class="label" for="lat">Latitude</label>
          <input id="lat" v-model.number="form.latitude" type="number" step="any" class="field" required />
        </div>
        <div>
          <label class="label" for="lon">Longitude</label>
          <input id="lon" v-model.number="form.longitude" type="number" step="any" class="field" required />
        </div>
        <div>
          <label class="label" for="radius">Risk radius (km)</label>
          <input id="radius" v-model.number="form.radius_km" type="number" step="any" min="0.1" class="field" required />
        </div>
        <div>
          <label class="label" for="mag">Min. magnitude</label>
          <input id="mag" v-model.number="form.min_magnitude" type="number" step="0.1" min="0" max="10" class="field" required />
        </div>
      </div>

      <div class="mt-4">
        <label class="label" for="instruction">Evacuation instruction</label>
        <textarea
          id="instruction"
          v-model="form.evacuation_instruction"
          class="field"
          rows="2"
          placeholder="Assembly point, stairwell, floor warden…"
        />
      </div>

      <label class="mt-4 flex items-center gap-2 text-sm">
        <input v-model="form.is_active" type="checkbox" class="accent-ink-900" /> Active
      </label>

      <div class="mt-5 flex justify-end gap-2">
        <button type="button" class="btn-ghost" @click="showForm = false">Cancel</button>
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </form>

    <div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      <article v-for="location in locations" :key="location.id" class="card card-pad">
        <div class="flex items-start justify-between gap-2">
          <div>
            <h2 class="font-semibold">{{ location.name }}</h2>
            <p class="text-xs text-ink-500">{{ location.address || location.code }}</p>
          </div>
          <span
            class="badge"
            :class="location.is_active
              ? 'bg-ok-100 text-ok-700 dark:bg-ok-700/20 dark:text-ok-100'
              : 'bg-ink-100 text-ink-500 dark:bg-ink-800'"
          >{{ location.is_active ? 'Active' : 'Inactive' }}</span>
        </div>

        <dl class="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt class="text-xs text-ink-500">Trigger at</dt>
            <dd class="font-semibold tabular-nums">M{{ location.min_magnitude }}+</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-500">Risk radius</dt>
            <dd class="font-semibold tabular-nums">{{ location.radius_km }} km</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-500">Coordinates</dt>
            <dd class="font-mono text-xs">{{ location.latitude }}, {{ location.longitude }}</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-500">Personnel</dt>
            <dd class="font-semibold tabular-nums">{{ location.employee_count }}</dd>
          </div>
        </dl>

        <p v-if="location.evacuation_instruction" class="mt-3 text-xs text-ink-500">
          {{ location.evacuation_instruction }}
        </p>

        <button class="btn-ghost mt-4 w-full" @click="startEdit(location)">Edit</button>
      </article>

      <p v-if="!locations.length" class="text-sm text-ink-500">
        No locations yet. Add one to define a geofence.
      </p>
    </div>
  </div>
</template>