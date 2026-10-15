import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Follows the backend ERP_PORT convention (dev.sh exports it); 5050 is the dev.sh default.
const devProxyTarget =
  process.env.VITE_DEV_PROXY_TARGET ||
  (process.env.ERP_PORT ? `http://127.0.0.1:${process.env.ERP_PORT}` : 'http://127.0.0.1:5050')
const devPort = Number(process.env.VITE_DEV_PORT || 3000)

export default defineConfig(({ mode }) => {
  const isDesktopBuild = mode === 'desktop'
  // 构建时阻止把旧依赖声明随新版本发行；版本号直接使用 package.json。
  const notices = JSON.parse(readFileSync(new URL('./src/legal/third-party-notices.json', import.meta.url), 'utf8'))
  const digest = createHash('sha256')
  for (const file of notices.inputFiles) {
    digest.update(file)
    digest.update(readFileSync(new URL(`../${file}`, import.meta.url)))
  }
  if (digest.digest('hex') !== notices.inputDigest) {
    throw new Error('第三方声明已过期，请在仓库根目录运行 .venv/bin/python scripts/generate_third_party_notices.py')
  }

  return {
    base: isDesktopBuild ? './' : '/',
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
            vue: ['vue', 'vue-router', 'pinia', 'vue-i18n'],
            charts: ['chart.js', 'vue-chartjs'],
          },
        },
      },
    },
  }
})
