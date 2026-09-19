<script setup>
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="grid place-items-center py-24 text-center">
    <div>
      <p class="text-5xl font-bold text-ink-300 dark:text-ink-700">404</p>
      <h1 class="mt-3 text-lg font-semibold">That page does not exist</h1>
      <RouterLink to="/" class="btn-primary mt-5">Back to overview</RouterLink>
    </div>
  </div>
</template>
