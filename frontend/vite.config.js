import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The build emits into frontend/dist, which Django adds to STATICFILES_DIRS
// and serves through WhiteNoise. `base` must match Django's STATIC_URL so the
// hashed asset links in index.html resolve once Django is serving the shell.
export default defineConfig({
  plugins: [vue()],
  base: '/static/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'spa',
    sourcemap: false,
  },
  server: {
    port: 5173,
    // Proxy the API in dev so the SPA stays same-origin and session cookies
    // and CSRF keep working without any CORS configuration.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/admin': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
