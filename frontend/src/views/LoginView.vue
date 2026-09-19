<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import AlertBanner from '@/components/AlertBanner.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const username = ref('')
const password = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await session.login(username.value, password.value)
    router.push(route.query.next || { name: 'overview' })
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <div class="grid min-h-full place-items-center px-4 py-12">
    <div class="w-full max-w-sm">
      <div class="mb-6 text-center">
        <span
          class="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-xl bg-alert-600 text-base font-bold text-white"
          aria-hidden="true"
        >CT</span>
        <h1 class="text-xl font-bold">DSO Automated Call Tree</h1>
        <p class="mt-1 text-sm text-ink-500 dark:text-ink-400">
          Safety Officer sign-in
        </p>
      </div>

      <form class="card card-pad" @submit.prevent="submit">
        <AlertBanner :message="error" @dismiss="error = ''" />

        <label class="label" for="username">Username</label>
        <input
          id="username"
          v-model="username"
          class="field mb-4"
          autocomplete="username"
          required
          autofocus
        />

        <label class="label" for="password">Password</label>
        <input
          id="password"
          v-model="password"
          type="password"
          class="field mb-5"
          autocomplete="current-password"
          required
        />

        <button class="btn-primary w-full" type="submit" :disabled="session.loading">
          {{ session.loading ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>

      <p class="mt-4 text-center text-xs text-ink-500 dark:text-ink-400">
        Responding to a drill? Use the link in your SMS or email alert.
      </p>
    </div>
  </div>
</template>
