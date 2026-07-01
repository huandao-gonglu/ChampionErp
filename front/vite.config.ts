import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:5050'
const devPort = Number(process.env.VITE_DEV_PORT || 3000)

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: devPort,
    proxy: {
      '/api': devProxyTarget,
      '/file': devProxyTarget,
      '/auth/mercadolibre': devProxyTarget,
      '/auth/wildberries': devProxyTarget,
      '/auth/ozon': devProxyTarget,
    },
  },
  build: {
    outDir: '../erp_web/static/dist',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia', 'vue-i18n'],
          charts: ['chart.js', 'vue-chartjs'],
        },
      },
    },
  },
})
