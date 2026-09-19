<script setup>
/**
 * The employee-facing page reached from the unique link in an alert.
 *
 * Designed for one-handed use on a phone during an evacuation: two very large
 * targets, no login, no navigation.
 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const token = route.params.token

const info = ref(null)
const error = ref('')
const submitting = ref('')
const done = ref(false)

async function load() {
  try {
    info.value = await api.get(`/api/status/${token}/`)
    done.value = ['safe', 'help'].includes(info.value.status)
  } catch (err) {
    error.value = err.message
  }
}

async function respond(status) {
  submitting.value = status
  error.value = ''
  try {
    const result = await api.post(`/api/status/${token}/`, { status })
    info.value = { ...info.value, ...result }
    done.value = true
  } catch (err) {
    error.value = err.message
  } finally {
    submitting.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="grid min-h-full place-items-center px-4 py-10">
    <div class="w-full max-w-md">
      <div v-if="error && !info" class="card card-pad text-center">
        <p class="text-sm text-alert-600">{{ error }}</p>
      </div>

      <div v-else-if="info" class="card card-pad">
        <p class="text-xs font-semibold uppercase tracking-wide text-alert-600">
          {{ info.drill_kind === 'incident' ? 'Emergency alert' : 'Earthquake drill' }}
        </p>
        <h1 class="mt-1 text-xl font-bold">Hello, {{ info.employee_name }}</h1>
        <p class="mt-1 text-sm text-ink-500 dark:text-ink-400">
          {{ info.drill_name }} · {{ info.location }}
        </p>

        <p
          v-if="info.magnitude"
          class="mt-3 rounded-lg bg-ink-100 px-3 py-2 text-sm dark:bg-ink-800"
        >
          Magnitude <strong>{{ info.magnitude }}</strong>
          <span v-if="info.place"> near {{ info.place }}</span>
        </p>

        <p v-if="info.instruction" class="mt-3 text-sm leading-relaxed">
          {{ info.instruction }}
        </p>

        <div v-if="done" class="mt-6 rounded-lg border border-ok-500/40 bg-ok-50 p-4 text-center dark:bg-ok-700/15">
          <p class="text-base font-bold text-ok-700 dark:text-ok-100">
            {{ info.status === 'help' ? 'Help is on the way' : 'You are marked safe' }}
          </p>
          <p class="mt-1 text-xs text-ok-700/80 dark:text-ok-100/80">
            Recorded {{ formatDateTime(info.responded_at) }}
          </p>
          <button
            v-if="info.status === 'safe' && info.is_open"
            class="btn-danger mt-4 w-full"
            :disabled="submitting"
            @click="respond('help')"
          >I still need help</button>
        </div>

        <div v-else-if="!info.is_open" class="mt-6 rounded-lg bg-ink-100 p-4 text-center text-sm dark:bg-ink-800">
          This drill is closed. Contact your Safety Officer directly.
        </div>

        <div v-else class="mt-6 space-y-3">
          <p class="text-center text-sm font-medium">Once you are secure, tap one:</p>
          <button
            class="w-full rounded-xl bg-ok-600 py-5 text-lg font-bold text-white transition
                   hover:bg-ok-700 disabled:opacity-60"
            :disabled="!!submitting"
            @click="respond('safe')"
          >
            {{ submitting === 'safe' ? 'Sending…' : "I'm SAFE" }}
          </button>
          <button
            class="w-full rounded-xl bg-alert-600 py-5 text-lg font-bold text-white transition
                   hover:bg-alert-700 disabled:opacity-60"
            :disabled="!!submitting"
            @click="respond('help')"
          >
            {{ submitting === 'help' ? 'Sending…' : 'I need HELP' }}
          </button>
          <p class="text-center text-xs text-ink-500 dark:text-ink-400">
            You can also reply SAFE or HELP to the SMS.
          </p>
        </div>

        <p v-if="error" class="mt-4 text-center text-sm text-alert-600">{{ error }}</p>
      </div>

      <div v-else class="card card-pad text-center text-sm text-ink-500">Loading…</div>
    </div>
  </div>
</template>
