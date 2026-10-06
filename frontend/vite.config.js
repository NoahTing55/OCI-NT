import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// /api 代理到后端；docker 内用 VITE_API_TARGET=http://api:8000，本地开发默认 localhost:8000
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
