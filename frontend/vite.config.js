import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 单端口部署：生产环境由 FastAPI 直接托管 build 产物（dist），同源访问 /api，无跨域。
// client.js 已用相对路径 baseURL '/api'，不依赖任何环境变量。
// 下面的 server.proxy 仅本地开发（npm run dev）用，生产 build 不受影响。
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
  },
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
