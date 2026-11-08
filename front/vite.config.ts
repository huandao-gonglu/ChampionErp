import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { thirdPartyNotices } from './build/third-party-notices'

// Follows the backend ERP_PORT convention (dev.sh exports it); 5050 is the dev.sh default.
const devProxyTarget =
  process.env.VITE_DEV_PROXY_TARGET ||
  (process.env.ERP_PORT ? `http://127.0.0.1:${process.env.ERP_PORT}` : 'http://127.0.0.1:5050')
const devPort = Number(process.env.VITE_DEV_PORT || 3000)

export default defineConfig(({ mode }) => {
  const isDesktopBuild = mode === 'desktop'

  return {
    base: isDesktopBuild ? './' : '/',
    plugins: [vue(), thirdPartyNotices(isDesktopBuild)],
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
        '/auth/alibaba/callback': devProxyTarget,
        '/auth/mercadolibre': devProxyTarget,
        '/auth/ozon': devProxyTarget,
      },
    },
    build: {
      outDir: isDesktopBuild ? 'dist-desktop' : '../erp_web/static/dist',
      emptyOutDir: true,
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia'],
            charts: ['chart.js', 'vue-chartjs'],
          },
        },
      },
    },
  }
})
