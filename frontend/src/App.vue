<script setup>
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const menuOpen = ref(false)

// Login and the employee status page render bare, without the admin shell.
const showChrome = computed(() => route.meta.chrome !== false && session.authenticated)

const links = [
  { to: '/', label: 'Overview', icon: 'M3 12l9-9 9 9M5 10v10h14V10' },
  { to: '/drills', label: 'Drills', icon: 'M12 2v6m0 8v6M2 12h6m8 0h6' },
  { to: '/employees', label: 'Directory', icon: 'M16 21v-2a4 4 0 00-8 0v2M12 7a4 4 0 100 8 4 4 0 000-8z' },
  { to: '/locations', label: 'Geofences', icon: 'M12 21s7-6.2 7-11a7 7 0 10-14 0c0 4.8 7 11 7 11z' },
  { to: '/events', label: 'Seismic feed', icon: 'M2 12h4l3-8 4 16 3-8h6' },
  { to: '/health', label: 'System health', icon: 'M20 12a8 8 0 11-16 0 8 8 0 0116 0zM12 8v4l3 2' },
]

async function signOut() {
  await session.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div v-if="showChrome" class="flex min-h-full flex-col lg:flex-row">
    <!-- Sidebar -->
    <aside
      class="shrink-0 border-b border-ink-200 bg-white lg:w-60 lg:border-b-0 lg:border-r
             dark:border-ink-800 dark:bg-ink-900"
    >
      <div class="flex items-center justify-between gap-3 px-5 py-4">
        <div class="flex items-center gap-2.5">
          <span
            class="grid h-9 w-9 place-items-center rounded-lg bg-alert-600 text-sm font-bold text-white"
            aria-hidden="true"
          >CT</span>
          <div class="leading-tight">
            <p class="text-sm font-bold">DSO Call Tree</p>
            <p class="text-xs text-ink-500 dark:text-ink-400">Earthquake response</p>
          </div>
        </div>
        <button
          class="btn-ghost px-2 py-1 lg:hidden"
          :aria-expanded="menuOpen"
          aria-label="Toggle navigation"
          @click="menuOpen = !menuOpen"
        >
          <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 6h16M4 12h16M4 18h16" stroke-linecap="round" />
          </svg>
        </button>
      </div>

      <nav
        class="px-3 pb-4 lg:block"
        :class="menuOpen ? 'block' : 'hidden'"
        aria-label="Main"
      >
        <RouterLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          class="mb-0.5 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium
                 text-ink-600 transition hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
          active-class="bg-ink-900 text-white hover:bg-ink-900 dark:bg-ink-100 dark:text-ink-900 dark:hover:bg-ink-100"
          @click="menuOpen = false"
        >
          <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path :d="link.icon" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ link.label }}
        </RouterLink>

        <div class="mt-4 border-t border-ink-200 pt-3 dark:border-ink-800">
          <p class="px-3 text-xs text-ink-500 dark:text-ink-400">
            Signed in as <span class="font-semibold text-ink-700 dark:text-ink-200">{{ session.displayName }}</span>
          </p>
          <div class="mt-2 flex flex-col gap-1 px-1">
            <a
              href="/admin/"
              class="rounded-lg px-2 py-1.5 text-sm text-ink-600 hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
            >Django admin</a>
            <button
              class="rounded-lg px-2 py-1.5 text-left text-sm text-ink-600 hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
              @click="signOut"
            >Sign out</button>
          </div>
        </div>
      </nav>
    </aside>

    <main class="min-w-0 flex-1">
      <div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <RouterView />
      </div>
    </main>
  </div>

  <RouterView v-else />
</template>
