import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const backend = 'http://127.0.0.1:8765'

export default defineConfig(({ command }) => ({
  plugins: [vue(), {
    name: 'nreact-local-session',
    async transformIndexHtml(html) {
      if (command === 'build') return html
      const response = await fetch(backend)
      const page = await response.text()
      const token = page.match(/name="nreact-token" content="([^"]+)"/)?.[1]
      if (!token) throw new Error('Start nreact ui --no-browser before the Vite dev server.')
      return html.replace('__NREACT_TOKEN__', token)
    },
  }],
  build: {
    outDir: fileURLToPath(new URL('../python/nreact/web_static', import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: backend,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (request) => request.setHeader('Origin', backend))
        },
      },
    },
  },
}))
