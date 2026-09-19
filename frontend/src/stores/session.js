import { defineStore } from 'pinia'
import { api } from '@/api/client'

export const useSessionStore = defineStore('session', {
  state: () => ({
    user: null,
    authenticated: false,
    resolved: false,
    loading: false,
  }),

  getters: {
    displayName: (state) =>
      state.user?.first_name || state.user?.username || 'Safety Officer',
  },

  actions: {
    /**
     * Ask the server who we are. Also seeds the CSRF cookie, so this must run
     * before the first unsafe request of the session.
     */
    async fetch() {
      try {
        const data = await api.get('/api/auth/session/')
        this.user = data.user
        this.authenticated = data.authenticated
      } catch {
        this.user = null
        this.authenticated = false
      } finally {
        this.resolved = true
      }
      return this.authenticated
    },

    async login(username, password) {
      this.loading = true
      try {
        const data = await api.post('/api/auth/login/', { username, password })
        this.user = data.user
        this.authenticated = true
        return data
      } finally {
        this.loading = false
      }
    },

    async logout() {
      try {
        await api.post('/api/auth/logout/')
      } finally {
        this.user = null
        this.authenticated = false
      }
    },
  },
})
