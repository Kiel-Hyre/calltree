<script setup>
/** System Health Verification, run before a drill goes live. */
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import PageHeader from '@/components/PageHeader.vue'
import AlertBanner from '@/components/AlertBanner.vue'

const health = ref(null)
const error = ref('')
const loading = ref(false)

const sections = computed(() => {
  if (!health.value) return []
  const labels = {
    database: 'Database',
    sms_gateway: 'M360 SMS gateway',
    email: 'SMTP email',
    pubsub: 'Cloud Pub/Sub',
    firestore: 'Cloud Firestore',
    queue: 'Notification queue',
  }
  return Object.entries(labels).map(([key, label]) => ({
    key,
    label,
    data: health.value[key] ?? {},
  }))
})

async function load() {
  loading.value = true
  try {
    health.value = await api.get('/api/health/')
    error.value = ''
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="System health"
      subtitle="Verify gateways and cloud services before initiating a drill"
    >
      <template #actions>
        <button class="btn-primary" :disabled="loading" @click="load">
          {{ loading ? 'Checking…' : 'Re-check' }}
        </button>
      </template>
    </PageHeader>

    <AlertBanner :message="error" @dismiss="error = ''" />

    <div
      v-if="health"
      class="mb-5 rounded-lg border px-4 py-3 text-sm"
      :class="health.ok
        ? 'border-ok-500/40 bg-ok-50 text-ok-700 dark:border-ok-700 dark:bg-ok-700/15 dark:text-ok-100'
        : 'border-warn-500/40 bg-warn-50 text-warn-700 dark:border-warn-700 dark:bg-warn-700/15 dark:text-warn-100'"
    >
      <strong>{{ health.ok ? 'All systems ready.' : 'Attention needed.' }}</strong>
      {{ health.ok
        ? 'The call tree can be initiated.'
        : 'Review the failing checks below before running a live drill.' }}
    </div>

    <div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      <article v-for="section in sections" :key="section.key" class="card card-pad">
        <div class="flex items-center justify-between gap-2">
          <h2 class="font-semibold">{{ section.label }}</h2>
          <span
            class="badge"
            :class="section.data.ok
              ? 'bg-ok-100 text-ok-700 dark:bg-ok-700/20 dark:text-ok-100'
              : 'bg-alert-100 text-alert-700 dark:bg-alert-700/25 dark:text-alert-100'"
          >{{ section.data.ok ? 'OK' : 'Check' }}</span>
        </div>

        <dl class="mt-3 space-y-1.5 text-xs">
          <div
            v-for="[key, value] in Object.entries(section.data).filter(([k]) => !['ok', 'raw'].includes(k))"
            :key="key"
            class="flex justify-between gap-3"
          >
            <dt class="text-ink-500">{{ key.replace(/_/g, ' ') }}</dt>
            <dd class="truncate text-right font-mono">{{ value === null ? '—' : String(value) }}</dd>
          </div>
        </dl>
      </article>
    </div>

    <p v-if="!health && !error" class="text-sm text-ink-500">Running checks…</p>
  </div>
</template>
