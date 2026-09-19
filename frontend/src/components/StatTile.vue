<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  hint: { type: String, default: '' },
  tone: { type: String, default: 'neutral' }, // neutral | ok | warn | alert
})

const TONES = {
  neutral: 'text-ink-900 dark:text-ink-50',
  ok: 'text-ok-600 dark:text-ok-500',
  warn: 'text-warn-600 dark:text-warn-500',
  alert: 'text-alert-600 dark:text-alert-500',
}
</script>

<template>
  <div class="card card-pad">
    <p class="text-xs font-semibold uppercase tracking-wide text-ink-500 dark:text-ink-400">
      {{ label }}
    </p>
    <p class="mt-1.5 text-3xl font-bold tabular-nums" :class="TONES[tone]">{{ value }}</p>
    <p v-if="hint" class="mt-1 text-xs text-ink-500 dark:text-ink-400">{{ hint }}</p>
  </div>
</template>
