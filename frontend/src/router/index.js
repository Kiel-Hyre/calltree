import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, chrome: false },
  },
  {
    // The employee-facing page. Public by design: the token in the URL is
    // the credential, so a member can respond without an account.
    path: '/status/:token',
    name: 'status',
    component: () => import('@/views/StatusView.vue'),
    meta: { public: true, chrome: false },
  },
  { path: '/', name: 'overview', component: () => import('@/views/OverviewView.vue') },
  { path: '/drills', name: 'drills', component: () => import('@/views/DrillsView.vue') },
  {
    path: '/drills/:id',
    name: 'drill-monitor',
    component: () => import('@/views/DrillMonitorView.vue'),
    props: true,
  },
  { path: '/employees', name: 'employees', component: () => import('@/views/EmployeesView.vue') },
  { path: '/locations', name: 'locations', component: () => import('@/views/LocationsView.vue') },
  { path: '/events', name: 'events', component: () => import('@/views/EventsView.vue') },
  { path: '/health', name: 'health', component: () => import('@/views/HealthView.vue') },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
]

const router = createRouter({
  history: createWebHistory('/'),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  if (!session.resolved) await session.fetch()

  if (to.meta.public) {
    // Already signed in? Skip the login form.
    if (to.name === 'login' && session.authenticated) return { name: 'overview' }
    return true
  }

  if (!session.authenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  return true
})

export default router
