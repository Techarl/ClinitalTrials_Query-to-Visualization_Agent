import { defineConfig } from 'vite'

export default defineConfig({
  server: { proxy: { '/v2': 'http://127.0.0.1:8001', '/health': 'http://127.0.0.1:8001', '/capabilities': 'http://127.0.0.1:8001' } },
  build: { outDir: 'dist', rolldownOptions: { output: { manualChunks: (id) => id.includes('echarts') || id.includes('zrender') ? 'charts' : undefined } } },
})
