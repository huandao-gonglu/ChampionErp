import { fileURLToPath, URL } from 'node:url'
import { createRequire } from 'node:module'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { thirdPartyNotices } from './build/third-party-notices'
const desktopDevelopmentModule = createRequire(import.meta.url)('./desktop/development.cjs') as typeof import('./desktop/development.cjs')

// Follows the backend ERP_PORT convention (dev.sh exports it); 5050 is the dev.sh default.
const devProxyTarget =
  process.env.VITE_DEV_PROXY_TARGET ||
  (process.env.ERP_PORT ? `http://127.0.0.1:${process.env.ERP_PORT}` : 'http://127.0.0.1:5050')
const devPort = Number(process.env.VITE_DEV_PORT || 3000)

export default defineConfig(({ mode }) => {
  const isDesktopBuild = mode === 'desktop'
  const desktopDevelopment = mode === 'desktop-dev' || process.env.ERP_DESKTOP_DEV === '1'

  return {
    base: isDesktopBuild ? './' : '/',
    plugins: [vue(), thirdPartyNotices(isDesktopBuild || desktopDevelopment),
      desktopDevelopmentModule.desktopDevelopment({ enabled: desktopDevelopment, backendUrl: devProxyTarget })],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '127.0.0.1',
      port: devPort,
      strictPort: true,
      hmr: { clientPort: devPort },
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
