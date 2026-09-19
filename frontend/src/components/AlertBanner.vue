<script setup>
defineProps({
  message: { type: String, default: '' },
  tone: { type: String, default: 'alert' }, // alert | ok | warn | info
  details: { type: Array, default: () => [] },
})
defineEmits(['dismiss'])

const TONES = {
  alert: 'border-alert-300 bg-alert-50 text-alert-700 dark:border-alert-700 dark:bg-alert-700/15 dark:text-alert-100',
  ok: 'border-ok-500/40 bg-ok-50 text-ok-700 dark:border-ok-700 dark:bg-ok-700/15 dark:text-ok-100',
  warn: 'border-warn-500/40 bg-warn-50 text-warn-700 dark:border-warn-700 dark:bg-warn-700/15 dark:text-warn-100',
  info: 'border-ink-300 bg-ink-100 text-ink-700 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-200',
}
</script>

<template>
  <div
    v-if="message"
    class="mb-4 flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm"
    :class="TONES[tone]"
    role="status"
  >
    <div>
      <p class="font-medium">{{ message }}</p>
      <ul v-if="details.length" class="mt-1 list-inside list-disc text-xs opacity-90">
        <li v-for="detail in details" :key="detail">{{ detail }}</li>
      </ul>
    </div>
    <button
      class="shrink-0 rounded px-1 text-lg leading-none opacity-60 hover:opacity-100"
      aria-label="Dismiss"
      @click="$emit('dismiss')"
    >&times;</button>
  </div>
</template>
