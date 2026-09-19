<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, listOf } from '@/api/client'
import PageHeader from '@/components/PageHeader.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const employees = ref([])
const locations = ref([])
const error = ref('')
const details = ref([])
const search = ref('')
const showForm = ref(false)
const editingId = ref(null)
const saving = ref(false)

const blank = () => ({
  employee_id: '',
  full_name: '',
  email: '',
  mobile_number: '',
  location: null,
  department: 'Digital Service Operations',
  role: 'staff',
  escalation_tier: 3,
  sms_opt_in: true,
  email_opt_in: true,
  is_active: true,
})
const form = reactive(blank())

const ROLES = [
  { value: 'staff', label: 'DSO Staff' },
  { value: 'floor_warden', label: 'Floor Warden' },
  { value: 'emt', label: 'Emergency Management Team' },
  { value: 'safety_officer', label: 'Safety Officer' },
]

async function load() {
  try {
    const [employeePage, locationPage] = await Promise.all([
      api.get('/api/employees/', { search: search.value }),
      api.get('/api/locations/'),
    ])
    employees.value = listOf(employeePage)
    locations.value = listOf(locationPage)
  } catch (err) {
    error.value = err.message
  }
}

function startCreate() {
  Object.assign(form, blank(), { location: locations.value[0]?.id ?? null })
  editingId.value = null
  showForm.value = true
}

function startEdit(employee) {
  Object.assign(form, employee)
  editingId.value = employee.id
  showForm.value = true
}

async function save() {
  saving.value = true
  error.value = ''
  details.value = []
  try {
    if (editingId.value) {
      await api.put(`/api/employees/${editingId.value}/`, { ...form })
    } else {
      await api.post('/api/employees/', { ...form })
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

async function remove(employee) {
  if (!window.confirm(`Remove ${employee.full_name} from the directory?`)) return
  try {
    await api.delete(`/api/employees/${employee.id}/`)
    await load()
  } catch (err) {
    error.value = err.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader title="Personnel directory" subtitle="Contact routes and escalation tiers">
      <template #actions>
        <input v-model="search" class="field w-48" placeholder="Search name…" @input="load" />
        <button class="btn-primary" @click="startCreate">Add person</button>
      </template>
    </PageHeader>

    <AlertBanner :message="error" :details="details" @dismiss="error = ''" />

    <form v-if="showForm" class="card card-pad mb-6" @submit.prevent="save">
      <h2 class="mb-4 text-sm font-semibold uppercase tracking-wide text-ink-500">
        {{ editingId ? 'Edit person' : 'New person' }}
      </h2>

      <div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <div>
          <label class="label" for="employee_id">Employee ID</label>
          <input id="employee_id" v-model="form.employee_id" class="field" required />
        </div>
        <div>
          <label class="label" for="full_name">Full name</label>
          <input id="full_name" v-model="form.full_name" class="field" required />
        </div>
        <div>
          <label class="label" for="mobile">Mobile number</label>
          <input id="mobile" v-model="form.mobile_number" class="field" placeholder="+639171234567" required />
        </div>
        <div>
          <label class="label" for="email">Email</label>
          <input id="email" v-model="form.email" type="email" class="field" />
        </div>
        <div>
          <label class="label" for="location">Location</label>
          <select id="location" v-model="form.location" class="field" required>
            <option v-for="location in locations" :key="location.id" :value="location.id">
              {{ location.name }}
            </option>
          </select>
        </div>
        <div>
          <label class="label" for="role">Role</label>
          <select id="role" v-model="form.role" class="field">
            <option v-for="role in ROLES" :key="role.value" :value="role.value">{{ role.label }}</option>
          </select>
        </div>
        <div>
          <label class="label" for="tier">Escalation tier</label>
          <input id="tier" v-model.number="form.escalation_tier" type="number" min="1" class="field" />
        </div>
        <div>
          <label class="label" for="department">Department</label>
          <input id="department" v-model="form.department" class="field" />
        </div>
      </div>

      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <label class="flex items-center gap-2">
          <input v-model="form.sms_opt_in" type="checkbox" class="accent-ink-900" /> SMS
        </label>
        <label class="flex items-center gap-2">
          <input v-model="form.email_opt_in" type="checkbox" class="accent-ink-900" /> Email
        </label>
        <label class="flex items-center gap-2">
          <input v-model="form.is_active" type="checkbox" class="accent-ink-900" /> Active
        </label>
      </div>

      <div class="mt-5 flex justify-end gap-2">
        <button type="button" class="btn-ghost" @click="showForm = false">Cancel</button>
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </div>
    </form>

    <div class="card overflow-x-auto">
      <table class="w-full min-w-[48rem]">
        <thead class="border-b border-ink-200 dark:border-ink-800">
          <tr>
            <th class="table-head">Name</th>
            <th class="table-head">Contact</th>
            <th class="table-head">Location</th>
            <th class="table-head">Role</th>
            <th class="table-head">Tier</th>
            <th class="table-head text-right">Manage</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-ink-200 dark:divide-ink-800">
          <tr v-for="employee in employees" :key="employee.id" :class="employee.is_active ? '' : 'opacity-50'">
            <td class="table-cell">
              <p class="font-medium">{{ employee.full_name }}</p>
              <p class="text-xs text-ink-500">{{ employee.employee_id }}</p>
            </td>
            <td class="table-cell text-xs">
              <p class="font-mono">{{ employee.mobile_number }}</p>
              <p class="text-ink-500">{{ employee.email || 'no email' }}</p>
            </td>
            <td class="table-cell text-xs">{{ employee.location_name }}</td>
            <td class="table-cell text-xs">{{ employee.role_display }}</td>
            <td class="table-cell tabular-nums">{{ employee.escalation_tier }}</td>
            <td class="table-cell text-right">
              <div class="inline-flex gap-1">
                <button class="btn-ghost px-2 py-1 text-xs" @click="startEdit(employee)">Edit</button>
                <button class="btn-ghost px-2 py-1 text-xs text-alert-600" @click="remove(employee)">
                  Remove
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!employees.length">
            <td colspan="6" class="px-4 py-10 text-center text-sm text-ink-500">
              No personnel yet. Add someone, or run <code>manage.py seed_demo</code>.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
