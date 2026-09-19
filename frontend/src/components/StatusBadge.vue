<script setup>
import { computed } from 'vue'
import { statusClass } from '@/utils/format'

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: '' },
})

const text = computed(
  () => props.label || props.status.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()),
)
</script>

<template>
  <span class="badge" :class="statusClass(status)">
    <span class="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
    {{ text }}
  </span>
</template>
